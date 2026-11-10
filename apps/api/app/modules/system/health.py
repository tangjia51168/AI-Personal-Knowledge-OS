import asyncio
import logging
from collections.abc import Callable
from typing import Any

from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from app.integrations.storage import get_s3_client

logger = logging.getLogger(__name__)


def check_database() -> None:
    with get_engine().connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError("Application DB role must not bypass RLS")
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if revision != "0031_collections":
            raise RuntimeError("Database migrations are not current")
        for table in (
            "tenants",
            "tenant_members",
            "processing_jobs",
            "media_assets",
            "job_steps",
            "job_attempts",
            "ai_providers",
            "ai_usage",
            "user_settings",
            "audit_events",
            "auth_security_events",
            "source_batches",
            "sources",
            "source_snapshots",
            "note_runs",
            "notes",
            "note_revisions",
            "note_sections",
            "evidence_items",
            "knowledge_labels",
            "knowledge_concepts",
            "note_labels",
            "note_annotations",
            "note_links",
            "note_learning",
            "summary_checkpoints",
            "semantic_chunks",
            "notification_preferences",
            "notification_deliveries",
            "collections",
            "collection_items",
        ):
            policy_count = connection.execute(
                text(
                    "SELECT count(*) FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename = :table"
                ),
                {"table": table},
            ).scalar()
            rls = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = to_regclass(:table)"
                ),
                {"table": f"public.{table}"},
            ).one()
            if not (rls.relrowsecurity and rls.relforcerowsecurity and policy_count):
                raise RuntimeError("Tenant isolation baseline is missing")


def check_redis() -> None:
    with Redis.from_url(
        get_settings().redis_url.get_secret_value(),
        socket_connect_timeout=3,
        socket_timeout=3,
    ) as client:
        if not client.ping():
            raise RuntimeError("Redis ping failed")


def check_storage() -> None:
    get_s3_client().head_bucket(Bucket=get_settings().s3_bucket)


async def probe(name: str, check: Callable[[], None]) -> tuple[str, dict[str, str]]:
    try:
        await asyncio.to_thread(check)
        return name, {"status": "up"}
    except Exception:
        # Never return connection strings, bucket credentials or exception text to clients.
        logger.warning("Dependency probe failed: %s", name)
        return name, {"status": "down"}


async def readiness() -> dict[str, Any]:
    results = await asyncio.gather(
        probe("postgresql", check_database),
        probe("redis", check_redis),
        probe("storage", check_storage),
    )
    services = dict(results)
    return {
        "status": "ready" if all(v["status"] == "up" for v in services.values()) else "degraded",
        "services": services,
    }
