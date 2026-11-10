import time
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, BinaryIO, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import MediaAsset
from app.integrations.storage import get_s3_client
from app.modules.files.service import (
    MAX_FILE_BYTES,
    get_asset,
    metadata,
    remove,
    ticket_signature,
    upload,
    verify_ticket,
)
from app.modules.identity.access import SpaceAccess, member_access

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}/assets", tags=["files"])
Access = Annotated[SpaceAccess, Depends(member_access)]


@router.get("")
def list_assets(access: Access) -> dict[str, Any]:
    assets = access.actor.session.scalars(
        select(MediaAsset)
        .where(MediaAsset.tenant_id == access.tenant.id, MediaAsset.status != "legacy")
        .order_by(MediaAsset.created_at.desc())
        .limit(1000)
    )
    return {"data": [metadata(asset) for asset in assets]}


@router.post("", status_code=201)
async def upload_asset(
    request: Request, access: Access, filename: str = Query(default="file", max_length=200)
) -> dict[str, Any]:
    mime_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    # Raw body avoids multipart parsing or client-supplied S3 keys. Bound the actual
    # bytes, not just Content-Length; timeout also bounds slow/chunked uploads.
    import asyncio

    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as stream:
        size = 0
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, detail={"code": "FILE_SIZE_LIMIT"})
                    stream.write(chunk)
        except TimeoutError:
            raise HTTPException(408, detail={"code": "UPLOAD_TIMEOUT"}) from None
        stream.seek(0)
        data = await run_in_threadpool(
            upload,
            access.actor,
            access.tenant.id,
            cast(BinaryIO, stream),
            size,
            mime_type,
            filename,
        )
        return {"data": data}


@router.post("/{asset_id}/download-ticket")
def download_ticket(asset_id: UUID, access: Access) -> dict[str, Any]:
    asset = get_asset(access.actor.session, access.tenant.id, asset_id)
    if asset.status != "ready":
        raise HTTPException(409, detail={"code": "ASSET_NOT_READY"})
    expires = int(time.time()) + 60
    return {
        "data": {
            "expires": expires,
            "signature": ticket_signature(
                access.tenant.id, asset_id, access.actor.user.id, expires
            ),
        }
    }


@router.get("/{asset_id}/download")
def download_asset(
    asset_id: UUID, access: Access, expires: int, signature: str = Query(max_length=64)
) -> StreamingResponse:
    verify_ticket(access.tenant.id, asset_id, access.actor.user.id, expires, signature)
    asset = get_asset(access.actor.session, access.tenant.id, asset_id)
    if asset.status != "ready":
        raise HTTPException(409, detail={"code": "ASSET_NOT_READY"})
    try:
        result = get_s3_client().get_object(Bucket=get_settings().s3_bucket, Key=asset.storage_key)
    except Exception:
        raise HTTPException(503, detail={"code": "STORAGE_UNAVAILABLE"}) from None
    body = result["Body"]

    def chunks() -> Any:
        try:
            yield from body.iter_chunks(chunk_size=65536)
        finally:
            body.close()

    # Attachment only: user-supplied content is never executed inline on app origin.
    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{asset_id}"',
            "Content-Length": str(asset.size),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{asset_id}")
def delete_asset(asset_id: UUID, access: Access) -> dict[str, Any]:
    remove(access.actor, access.tenant.id, asset_id)
    return {"data": {"removed": True}}
