from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction, AuditEvent
from app.modules.identity.access import SpaceAccess


def record(
    session: Session,
    tenant_id: UUID,
    actor_id: UUID,
    action: AuditAction,
    resource_id: UUID,
) -> AuditEvent:
    """Join the caller's authorized transaction; never commit independently.

    Only stable identifiers and a closed action vocabulary are accepted. Business
    names, arbitrary metadata, secrets and content do not enter this journal.
    The database also binds inserts to the transaction's tenant and actor.
    """
    if not isinstance(action, AuditAction):
        raise ValueError("Unsupported audit action")
    event = AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action.value,
        resource_id=resource_id,
    )
    session.add(event)
    return event


def record_event(access: SpaceAccess, action: AuditAction, resource_id: UUID) -> AuditEvent:
    return record(access.actor.session, access.tenant.id, access.actor.user.id, action, resource_id)
