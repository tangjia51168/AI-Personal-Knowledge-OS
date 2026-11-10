from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, tuple_

from app.modules.audit.models import AuditEvent
from app.modules.identity.access import SpaceAccess, member_access, require_admin

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}/audit-events", tags=["audit"])
Access = Annotated[SpaceAccess, Depends(member_access)]


@router.get("")
def list_events(
    access: Access,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> dict[str, Any]:
    require_admin(access)
    statement = select(AuditEvent).where(AuditEvent.tenant_id == access.tenant.id)
    if cursor is not None:
        anchor = access.actor.session.scalar(statement.where(AuditEvent.id == cursor))
        if anchor is None:
            raise HTTPException(404, detail={"code": "AUDIT_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(AuditEvent.created_at, AuditEvent.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        access.actor.session.scalars(
            statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    return {
        "data": [
            {
                "id": str(event.id),
                "actor_id": str(event.actor_id),
                "action": event.action,
                "resource_id": str(event.resource_id),
                "created_at": event.created_at.isoformat(),
            }
            for event in page
        ],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }
