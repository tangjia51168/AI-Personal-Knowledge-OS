"""Opt-in personal inbox; at-most-one SMTP attempt with durable uncertain receipts."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import TenantMember, User
from app.db.session import get_engine
from app.integrations.mail import MailDeliveryError, send_learning_notification
from app.integrations.queue import get_task_queue
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import (
    ActorIdentity,
    authorized_transaction,
    locked_membership,
    recheck_identity,
    set_tenant,
    set_user,
)
from app.modules.learning.models import NoteLearning
from app.modules.learning.notification_models import (
    NotificationDelivery as Delivery,
)
from app.modules.learning.notification_models import (
    NotificationPreference as Preference,
)
from app.modules.learning.notification_models import (
    NotificationTick as Tick,
)
from app.modules.notes.models import Note


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=0, strict=True)
    enabled: StrictBool
    email: StrictBool
    consent: StrictBool


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: UUID
    consent: StrictBool


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def preference_data(row: Preference | None) -> dict:
    return {
        "version": row.version if row else 0,
        "enabled": bool(row and row.enabled),
        "email": bool(row and row.email),
    }


def delivery_data(row: Delivery) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "note_id": str(row.note_id) if row.note_id else None,
        "reminder_at": row.reminder_at.isoformat() if row.reminder_at else None,
        "email_state": row.email_state,
        "created_at": row.created_at.isoformat(),
        "read_at": row.read_at.isoformat() if row.read_at else None,
    }


def own(session: Session, tenant: UUID, user: UUID) -> Preference | None:
    return session.scalar(
        select(Preference).where(Preference.tenant_id == tenant, Preference.user_id == user)
    )


def inbox(identity: ActorIdentity, tenant: UUID) -> dict:
    with authorized_transaction(identity, tenant) as (session, _role):
        row = own(session, tenant, identity.user_id)
        rows = list(
            session.scalars(
                select(Delivery)
                .where(Delivery.tenant_id == tenant, Delivery.user_id == identity.user_id)
                .order_by(Delivery.created_at.desc(), Delivery.id.desc())
                .limit(51)
            )
        )
        user = session.get(User, identity.user_id)
        return {
            "preference": preference_data(row),
            "email_verified": bool(user and user.email_verified),
            "smtp_configured": bool(get_settings().smtp_host),
            "data": [delivery_data(r) for r in rows[:50]],
            "has_more": len(rows) > 50,
        }


def save_preference(identity: ActorIdentity, tenant: UUID, body: PreferenceInput) -> dict:
    if (body.enabled and not body.consent) or (body.email and not body.enabled):
        raise HTTPException(422, detail={"code": "NOTIFICATION_CONSENT_REQUIRED"})
    with authorized_transaction(identity, tenant) as (session, _role):
        row = own(session, tenant, identity.user_id)
        if body.expected_version != (row.version if row else 0):
            raise HTTPException(409, detail={"code": "NOTIFICATION_VERSION_CONFLICT"})
        user = session.get(User, identity.user_id)
        if body.email and (user is None or not user.email_verified or not get_settings().smtp_host):
            raise HTTPException(409, detail={"code": "NOTIFICATION_EMAIL_UNAVAILABLE"})
        if row and (row.enabled, row.email) == (body.enabled, body.email):
            return preference_data(row)
        if row is None:
            row = Preference(
                tenant_id=tenant,
                user_id=identity.user_id,
                enabled=body.enabled,
                email=body.email,
                version=1,
            )
            session.add(row)
            session.flush()
            session.add(
                Tick(
                    preference_id=row.id,
                    tenant_id=tenant,
                    user_id=identity.user_id,
                    enabled=body.enabled,
                    due_at=datetime.now(UTC),
                )
            )
        else:
            row.enabled, row.email, row.version = body.enabled, body.email, row.version + 1
            tick = session.get(Tick, row.id)
            if tick is None or (tick.tenant_id, tick.user_id) != (tenant, identity.user_id):
                raise HTTPException(409, detail={"code": "NOTIFICATION_INDEX_INVALID"})
            tick.enabled, tick.due_at = body.enabled, datetime.now(UTC)
            for delivery in session.scalars(
                select(Delivery).where(
                    Delivery.tenant_id == tenant,
                    Delivery.preference_id == row.id,
                    Delivery.email_state == "pending",
                )
            ):
                delivery.email_state, delivery.finished_at = "cancelled", datetime.now(UTC)
        record(session, tenant, identity.user_id, AuditAction.NOTIFICATION_PREFERENCE, row.id)
        recheck_identity(session, identity)
        return preference_data(row)


def append(
    session: Session, preference: Preference, occurrence: str, learning: NoteLearning | None = None
) -> Delivery:
    previous = session.scalar(
        select(Delivery).where(
            Delivery.tenant_id == preference.tenant_id,
            Delivery.user_id == preference.user_id,
            Delivery.occurrence == occurrence,
        )
    )
    if previous:
        return previous
    count = (
        session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(
                Delivery.tenant_id == preference.tenant_id, Delivery.user_id == preference.user_id
            )
        )
        or 0
    )
    if count >= 5000:
        raise HTTPException(429, detail={"code": "NOTIFICATION_QUOTA"})
    row = Delivery(
        id=uuid4(),
        tenant_id=preference.tenant_id,
        user_id=preference.user_id,
        preference_id=preference.id,
        preference_version=preference.version,
        occurrence=occurrence,
        kind="due" if learning else "test",
        note_id=learning.note_id if learning else None,
        reminder_at=learning.reminder_at if learning else None,
        email_state="pending" if preference.email else "not_requested",
    )
    session.add(row)
    session.flush()
    record(session, row.tenant_id, row.user_id, AuditAction.NOTIFICATION_CREATED, row.id)
    return row


def self_test(identity: ActorIdentity, tenant: UUID, body: TestInput) -> dict:
    if not body.consent:
        raise HTTPException(422, detail={"code": "NOTIFICATION_CONSENT_REQUIRED"})
    with authorized_transaction(identity, tenant) as (session, _role):
        row = own(session, tenant, identity.user_id)
        if row is None or not row.enabled:
            raise HTTPException(409, detail={"code": "NOTIFICATION_DISABLED"})
        key = "test:" + str(body.idempotency_key)
        previous = session.scalar(
            select(Delivery).where(
                Delivery.tenant_id == tenant,
                Delivery.user_id == identity.user_id,
                Delivery.occurrence == key,
            )
        )
        if previous:
            return delivery_data(previous)
        count = (
            session.scalar(
                select(func.count())
                .select_from(Delivery)
                .where(
                    Delivery.tenant_id == tenant,
                    Delivery.user_id == identity.user_id,
                    Delivery.kind == "test",
                    Delivery.created_at >= datetime.now(UTC) - timedelta(days=1),
                )
            )
            or 0
        )
        if count >= 3:
            raise HTTPException(429, detail={"code": "NOTIFICATION_TEST_QUOTA"})
        result = delivery_data(append(session, row, key))
        recheck_identity(session, identity)
        return result


def mark_read(identity: ActorIdentity, tenant: UUID, delivery: UUID) -> dict:
    with authorized_transaction(identity, tenant) as (session, _role):
        row = session.scalar(
            select(Delivery).where(
                Delivery.tenant_id == tenant,
                Delivery.user_id == identity.user_id,
                Delivery.id == delivery,
            )
        )
        if row is None:
            raise HTTPException(404, detail={"code": "NOTIFICATION_NOT_FOUND"})
        if row.read_at is None:
            row.read_at = datetime.now(UTC)
            record(session, tenant, identity.user_id, AuditAction.NOTIFICATION_READ, row.id)
        recheck_identity(session, identity)
        return delivery_data(row)


@contextmanager
def worker_context(preference_id: UUID) -> Iterator[tuple[Session, Preference, User] | None]:
    """Discovery UUID is not authority; validate stored relationships and membership anew."""
    with Session(get_engine()) as session, session.begin():
        tick = session.get(Tick, preference_id)
        if tick is None or not tick.enabled:
            yield None
            return
        set_user(session, tick.user_id)
        user = session.get(User, tick.user_id)
        member = session.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == tick.tenant_id,
                TenantMember.user_id == tick.user_id,
                TenantMember.active.is_(True),
            )
        )
        if user is None or user.status != "active" or member is None:
            tick.enabled = False
            yield None
            return
        set_tenant(session, tick.tenant_id)
        try:
            locked_membership(session, tick.tenant_id, tick.user_id)
        except HTTPException:
            tick.enabled = False
            yield None
            return
        session.refresh(user)
        session.refresh(tick)
        preference = own(session, tick.tenant_id, tick.user_id)
        if (
            not tick.enabled
            or preference is None
            or preference.id != preference_id
            or not preference.enabled
            or user.status != "active"
            or (get_settings().email_verification_required and not user.email_verified)
        ):
            tick.enabled = False
            yield None
            return
        yield session, preference, user


def dispatch_due() -> int:
    now, count = datetime.now(UTC), 0
    with Session(get_engine()) as session, session.begin():
        ticks = session.scalars(
            select(Tick)
            .where(Tick.enabled.is_(True), Tick.due_at <= now)
            .order_by(Tick.due_at)
            .limit(50)
            .with_for_update(skip_locked=True)
        )
        for tick in ticks:
            get_task_queue().send_task("knowledge_os.notifications", args=[str(tick.preference_id)])
            tick.due_at = now + timedelta(seconds=60)
            count += 1
    return count


def execute_due(value: str) -> None:
    preference_id = UUID(value)
    with worker_context(preference_id) as context:
        if context is None:
            return
        session, preference, _user = context
        now = datetime.now(UTC)
        # Exclude already-created occurrences before LIMIT so old due rows cannot
        # starve later reminders. timestamptz equality is independent of timezone.
        delivered = exists(
            select(Delivery.id).where(
                Delivery.tenant_id == NoteLearning.tenant_id,
                Delivery.user_id == NoteLearning.user_id,
                Delivery.note_id == NoteLearning.note_id,
                Delivery.kind == "due",
                Delivery.reminder_at == NoteLearning.reminder_at,
            )
        )
        session.execute(text("SET LOCAL statement_timeout='5s'"))
        due = session.scalars(
            select(NoteLearning)
            .join(
                Note, (Note.tenant_id == NoteLearning.tenant_id) & (Note.id == NoteLearning.note_id)
            )
            .where(
                NoteLearning.tenant_id == preference.tenant_id,
                NoteLearning.user_id == preference.user_id,
                NoteLearning.status != "deep_read",
                NoteLearning.reminder_at <= now,
                ~delivered,
            )
            .order_by(NoteLearning.reminder_at, NoteLearning.id)
            .limit(100)
        )
        for learning in due:
            assert learning.reminder_at is not None
            key = "due:" + str(learning.id) + ":" + learning.reminder_at.isoformat()
            # Fixed time is the occurrence: status edits alone do not re-notify.
            try:
                with session.begin_nested():
                    append(session, preference, key, learning)
            except HTTPException as error:
                if error.status_code != 429:
                    raise
                break
        # A process may die after sending; never put an uncertain attempt back in pending.
        for row in session.scalars(
            select(Delivery).where(
                Delivery.tenant_id == preference.tenant_id,
                Delivery.preference_id == preference_id,
                Delivery.email_state == "dispatched",
                Delivery.dispatched_at <= now - timedelta(minutes=2),
            )
        ):
            row.email_state, row.finished_at = "unknown", now
            record(session, row.tenant_id, row.user_id, AuditAction.NOTIFICATION_FINISHED, row.id)
        pending = list(
            session.scalars(
                select(Delivery.id)
                .where(
                    Delivery.tenant_id == preference.tenant_id,
                    Delivery.preference_id == preference_id,
                    Delivery.email_state == "pending",
                )
                .order_by(Delivery.created_at, Delivery.id)
                .limit(20)
            )
        )
    for delivery_id in pending:
        deliver_email(preference_id, delivery_id)


def deliver_email(preference_id: UUID, delivery_id: UUID) -> None:
    with worker_context(preference_id) as context:
        if context is None:
            return
        session, preference, user = context
        row = session.scalar(
            select(Delivery).where(
                Delivery.tenant_id == preference.tenant_id,
                Delivery.user_id == preference.user_id,
                Delivery.preference_id == preference_id,
                Delivery.id == delivery_id,
            )
        )
        if row is None or row.email_state != "pending":
            return
        valid = (
            preference.email
            and preference.version == row.preference_version
            and user.email_verified
        )
        if row.kind == "due":
            learning = session.scalar(
                select(NoteLearning).where(
                    NoteLearning.tenant_id == row.tenant_id,
                    NoteLearning.user_id == row.user_id,
                    NoteLearning.note_id == row.note_id,
                )
            )
            valid = bool(
                valid
                and learning
                and learning.status != "deep_read"
                and learning.reminder_at == row.reminder_at
                and row.reminder_at is not None
                and row.reminder_at <= datetime.now(UTC)
            )
        if not valid:
            row.email_state, row.finished_at = "cancelled", datetime.now(UTC)
            record(session, row.tenant_id, row.user_id, AuditAction.NOTIFICATION_FINISHED, row.id)
            return
        row.email_state, row.dispatched_at = "dispatched", datetime.now(UTC)
        recipient, is_test = user.email, row.kind == "test"
        record(session, row.tenant_id, row.user_id, AuditAction.NOTIFICATION_DISPATCH, row.id)
    # Durable claim commits before SMTP. No DB locks held during external I/O.
    # Revocation after this point cannot retract already authorized network bytes.
    try:
        send_learning_notification(recipient, self_test=is_test)
        state = "smtp_accepted"
    except MailDeliveryError:
        state = "unknown"
    with worker_context(preference_id) as context:
        if context is None:
            return  # Uncertain dispatched receipt remains; never resend it.
        session, preference, _user = context
        row = session.scalar(
            select(Delivery).where(
                Delivery.tenant_id == preference.tenant_id,
                Delivery.preference_id == preference_id,
                Delivery.id == delivery_id,
                Delivery.user_id == preference.user_id,
            )
        )
        if row and row.email_state == "dispatched":
            row.email_state, row.finished_at = state, datetime.now(UTC)
            record(session, row.tenant_id, row.user_id, AuditAction.NOTIFICATION_FINISHED, row.id)
