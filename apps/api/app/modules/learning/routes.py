from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.modules.identity.access import ActorIdentity, SpaceAccess, member_access
from app.modules.identity.rate_limit import rate_limit
from app.modules.learning import notifications
from app.modules.learning.analysis import AnalysisQuery, AnalysisResponse, analyze_learning
from app.modules.learning.calendar_export import render_calendar
from app.modules.learning.content import LearningUpdate, ReviewUpdate
from app.modules.learning.reminders import (
    ReminderBatch,
    ReminderPlan,
    ReminderSelection,
    apply_plan,
    apply_reminders,
    reminder_selection,
)
from app.modules.learning.schedule import ScheduleQuery, ScheduleResponse, read_schedule
from app.modules.learning.service import page, update
from app.modules.learning.summary import SummaryQuery, SummaryResponse, summarize_learning

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}", tags=["learning"])
Access = Annotated[SpaceAccess, Depends(member_access)]


def notification_identity(access: SpaceAccess, request: Request) -> tuple[ActorIdentity, UUID]:
    if request.query_params:
        from fastapi import HTTPException

        raise HTTPException(422, detail={"code": "INVALID_NOTIFICATION_QUERY"})
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant = access.tenant.id
    access.actor.session.rollback()
    return identity, tenant


@router.get("/learning/notifications")
def get_notifications(request: Request, access: Access) -> dict:
    identity, tenant = notification_identity(access, request)
    return {"data": notifications.inbox(identity, tenant)}


@router.put("/learning/notifications/preference")
def set_notifications(
    body: notifications.PreferenceInput, request: Request, access: Access
) -> dict:
    rate_limit(request, "notification_preference", str(access.actor.user.id))
    identity, tenant = notification_identity(access, request)
    return {"data": notifications.save_preference(identity, tenant, body)}


@router.post("/learning/notifications/test")
def test_notifications(body: notifications.TestInput, request: Request, access: Access) -> dict:
    rate_limit(request, "notification_test", str(access.actor.user.id))
    identity, tenant = notification_identity(access, request)
    return {"data": notifications.self_test(identity, tenant, body)}


@router.post("/learning/notifications/{delivery_id}/read")
def read_notification(
    delivery_id: UUID, body: notifications.EmptyInput, request: Request, access: Access
) -> dict:
    rate_limit(request, "notification_read", str(access.actor.user.id))
    identity, tenant = notification_identity(access, request)
    return {"data": notifications.mark_read(identity, tenant, delivery_id)}


@router.put("/notes/{note_id}/review")
def review_note(note_id: UUID, body: ReviewUpdate, request: Request, access: Access) -> dict:
    rate_limit(request, "learning_write", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return {"data": update(identity, tenant_id, note_id, body, expected=body.expected)}


@router.put("/learning/reminders/plan")
def plan_reminders(body: ReminderPlan, request: Request, access: Access) -> dict:
    rate_limit(request, "learning_write", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return {"data": apply_plan(identity, tenant_id, body)}


@router.post("/learning/reminders/preview")
def preview_reminders(body: ReminderSelection, access: Access) -> dict:
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return {"data": reminder_selection(identity, tenant_id, body)}


@router.put("/learning/reminders/batch")
def batch_reminders(body: ReminderBatch, request: Request, access: Access) -> dict:
    rate_limit(request, "learning_write", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return {"data": apply_reminders(identity, tenant_id, body)}


@router.get("/learning/schedule", response_model=ScheduleResponse)
def get_learning_schedule(
    access: Access, query: Annotated[ScheduleQuery, Query()]
) -> ScheduleResponse:
    return ScheduleResponse(data=read_schedule(access, query))


@router.get("/learning/calendar")
def export_learning_calendar(access: Access, query: Annotated[ScheduleQuery, Query()]) -> Response:
    schedule = read_schedule(access, query)
    return Response(
        render_calendar(schedule, access.tenant.id, access.actor.user.id),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="knowledge-review.ics"',
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'"
            ),
        },
    )


@router.get("/learning/summary", response_model=SummaryResponse)
def get_learning_summary(
    access: Access, query: Annotated[SummaryQuery, Query()]
) -> SummaryResponse:
    return SummaryResponse(data=summarize_learning(access))


@router.get("/learning/analysis", response_model=AnalysisResponse)
def get_learning_analysis(
    access: Access, query: Annotated[AnalysisQuery, Query()]
) -> AnalysisResponse:
    return AnalysisResponse(data=analyze_learning(access))


@router.get("/learning")
def list_learning(
    access: Access, due: bool = Query(default=False), note_id: UUID | None = None
) -> dict:
    return {
        "data": page(
            ActorIdentity(access.actor.user.id, access.actor.token_hash),
            access.tenant.id,
            due_only=due,
            note_id=note_id,
        )
    }


@router.put("/notes/{note_id}/learning")
def set_learning(note_id: UUID, body: LearningUpdate, request: Request, access: Access) -> dict:
    rate_limit(request, "learning_write", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return {
        "data": update(
            identity,
            tenant_id,
            note_id,
            body,
        )
    }
