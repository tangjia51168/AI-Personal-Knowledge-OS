import hashlib
import hmac
import re
import time
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MediaAsset
from app.integrations.storage import get_s3_client, tenant_object_key
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import Actor
from app.modules.identity.access import authorized_transaction as file_transaction

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SPACE_BYTES = 100 * 1024 * 1024
TYPES = {"text/plain", "application/pdf", "image/png", "image/jpeg"}


def metadata(asset: MediaAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "filename": asset.filename,
        "mime_type": asset.mime_type,
        "size": asset.size,
        "status": asset.status,
        "created_at": asset.created_at.isoformat(),
    }


def validate_file(stream: BinaryIO, size: int, mime_type: str) -> None:
    if size < 1 or size > MAX_FILE_BYTES:
        raise HTTPException(413, detail={"code": "FILE_SIZE_LIMIT"})
    if mime_type not in TYPES:
        raise HTTPException(415, detail={"code": "UNSUPPORTED_FILE_TYPE"})
    prefix = stream.read(16)
    stream.seek(0)
    signatures = {
        "application/pdf": b"%PDF-",
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/jpeg": b"\xff\xd8\xff",
    }
    if mime_type in signatures and not prefix.startswith(signatures[mime_type]):
        raise HTTPException(415, detail={"code": "FILE_TYPE_MISMATCH"})
    if mime_type == "text/plain":
        try:
            text = stream.read().decode("utf-8")
            if "\x00" in text:
                raise ValueError("Binary text")
        except (UnicodeError, ValueError):
            raise HTTPException(415, detail={"code": "INVALID_UTF8_TEXT"}) from None
        finally:
            stream.seek(0)


def get_asset(session: Session, tenant_id: UUID, asset_id: UUID) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.tenant_id == tenant_id, MediaAsset.id == asset_id)
    )
    if asset is None or asset.status == "legacy" or asset.uploaded_by is None:
        raise HTTPException(404, detail={"code": "ASSET_NOT_FOUND"})
    if asset.storage_key != tenant_object_key(tenant_id, asset_id):
        raise HTTPException(409, detail={"code": "ASSET_KEY_INVALID"})
    return asset


def upload(
    actor: Actor, tenant_id: UUID, stream: BinaryIO, size: int, mime_type: str, filename: str
) -> dict[str, Any]:
    validate_file(stream, size, mime_type)
    filename = re.sub(r"[^\w. -]", "_", filename)[:200].strip(". ") or "file"
    asset_id = uuid4()
    key = tenant_object_key(tenant_id, asset_id)
    with file_transaction(actor, tenant_id) as (session, _):
        count, total = session.execute(
            select(func.count(), func.coalesce(func.sum(MediaAsset.size), 0)).where(
                MediaAsset.tenant_id == tenant_id
            )
        ).one()
        if count >= 1000 or total + size > MAX_SPACE_BYTES:
            raise HTTPException(429, detail={"code": "STORAGE_QUOTA_EXCEEDED"})
        session.add(
            MediaAsset(
                id=asset_id,
                tenant_id=tenant_id,
                uploaded_by=actor.user.id,
                storage_key=key,
                mime_type=mime_type,
                size=size,
                status="pending",
                filename=filename,
            )
        )
    # The pending row is committed BEFORE writing S3. A crash never creates an
    # untracked object; pending assets stay charged and can be explicitly deleted.
    try:
        get_s3_client().put_object(
            Bucket=get_settings().s3_bucket,
            Key=key,
            Body=stream,
            ContentLength=size,
            ContentType=mime_type,
        )
    except Exception:
        raise HTTPException(
            503, detail={"code": "STORAGE_UNAVAILABLE", "asset_id": str(asset_id)}
        ) from None
    with file_transaction(actor, tenant_id) as (session, _):
        asset = get_asset(session, tenant_id, asset_id)
        if asset.status != "pending":
            raise HTTPException(409, detail={"code": "ASSET_STATE_CHANGED"})
        asset.status = "ready"
        record(session, tenant_id, actor.user.id, AuditAction.FILE_UPLOADED, asset_id)
        return metadata(asset)


def remove(actor: Actor, tenant_id: UUID, asset_id: UUID) -> None:
    with file_transaction(actor, tenant_id) as (session, role):
        asset = get_asset(session, tenant_id, asset_id)
        if asset.uploaded_by != actor.user.id and role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        # Pending uploads may still be writing. Only delete after the upload time
        # bound expires (15 min); prevents delete/upload races leaking objects.
        if asset.status == "pending" and time.time() - asset.created_at.timestamp() < 900:
            raise HTTPException(409, detail={"code": "UPLOAD_IN_PROGRESS"})
        asset.status = "deleting"
        key = asset.storage_key
    try:
        get_s3_client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
    except Exception:
        raise HTTPException(503, detail={"code": "STORAGE_UNAVAILABLE"}) from None
    with file_transaction(actor, tenant_id) as (session, _):
        removed = session.scalar(
            delete(MediaAsset)
            .where(
                MediaAsset.tenant_id == tenant_id,
                MediaAsset.id == asset_id,
                MediaAsset.status == "deleting",
            )
            .returning(MediaAsset.id)
        )
        if removed is not None:
            record(session, tenant_id, actor.user.id, AuditAction.FILE_DELETED, asset_id)


def ticket_signature(tenant_id: UUID, asset_id: UUID, user_id: UUID, expires: int) -> str:
    secret = get_settings().download_signing_key.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(503, detail={"code": "DOWNLOAD_SIGNING_UNAVAILABLE"})
    message = f"file-download-v1:{tenant_id}:{asset_id}:{user_id}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_ticket(
    tenant_id: UUID, asset_id: UUID, user_id: UUID, expires: int, signature: str
) -> None:
    now = int(time.time())
    if (
        expires <= now
        or expires > now + 60
        or not hmac.compare_digest(
            signature, ticket_signature(tenant_id, asset_id, user_id, expires)
        )
    ):
        raise HTTPException(403, detail={"code": "DOWNLOAD_TICKET_INVALID"})
