from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.identity.rate_limit import rate_limit
from app.modules.identity.recovery import consume_action, request_action
from app.modules.identity.routes import Credentials

router = APIRouter(prefix="/api/v1/auth", tags=["identity-recovery"])


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return Credentials.normalize_email(value)


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$", repr=False)


class ResetInput(ActionInput):
    password: str = Field(min_length=12, max_length=256, repr=False)


@router.post("/request-{kind}", status_code=202)
def request_link(
    kind: Literal["verification", "password-reset"],
    body: EmailInput,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    rate_limit(request, "recovery-request", body.email)
    # Queue only in this process after the response; delivery timing must not
    # reveal whether an address exists. No token enters Redis/task payloads.
    # A process crash can lose delivery; the user explicitly requests a new link.
    background_tasks.add_task(
        request_action, body.email, "verify_email" if kind == "verification" else "reset_password"
    )
    return {"data": {"message": "如果账户符合条件，将发送邮件。请检查邮箱或稍后重试。"}}


@router.post("/verify-email")
def verify_email(body: ActionInput, request: Request) -> dict:
    rate_limit(request, "recovery-consume")
    consume_action(body.token, "verify_email")
    return {"data": {"email_verified": True}}


@router.post("/reset-password")
def reset_password(body: ResetInput, request: Request) -> dict:
    rate_limit(request, "recovery-consume")
    consume_action(body.token, "reset_password", body.password)
    return {"data": {"password_reset": True, "sessions_revoked": True}}
