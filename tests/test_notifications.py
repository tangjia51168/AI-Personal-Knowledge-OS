"""Boundary checks and actual loopback SMTP; no external delivery claim."""

import subprocess
import sys
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from app.integrations import mail
from app.modules.learning.notifications import EmptyInput, PreferenceInput
from app.modules.learning.notifications import TestInput as NotificationTestInput
from pydantic import ValidationError
from test_mail_transport import configure, sink


def test_fresh_worker_import_resolves_notification_foreign_keys():
    code = (
        "from app.modules.learning import notifications as n;"
        "assert all(fk.column is not None "
        "for model in (n.Delivery,n.Preference,n.Tick,n.NoteLearning) "
        "for fk in model.__table__.foreign_keys)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize(
    "model,body",
    [
        (
            PreferenceInput,
            {"expected_version": True, "enabled": True, "email": False, "consent": True},
        ),
        (
            PreferenceInput,
            {"expected_version": 0, "enabled": "true", "email": False, "consent": True},
        ),
        (
            PreferenceInput,
            {"expected_version": -1, "enabled": True, "email": False, "consent": True},
        ),
        (
            PreferenceInput,
            {
                "expected_version": 0,
                "enabled": True,
                "email": False,
                "consent": True,
                "recipient": "attacker@example.invalid",
            },
        ),
        (NotificationTestInput, {"idempotency_key": str(uuid4()), "consent": "true"}),
        (NotificationTestInput, {"idempotency_key": "invalid", "consent": True}),
        (
            NotificationTestInput,
            {"idempotency_key": str(uuid4()), "consent": True, "body": "arbitrary"},
        ),
        (EmptyInput, {"read_at": "2026-01-01"}),
    ],
)
def test_notification_contract_rejects_coercion_and_recipient_or_content_override(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("self_test", [True, False])
def test_real_smtp_fixed_learning_template_without_note_content_or_token(monkeypatch, self_test):
    with sink() as server:
        configure(monkeypatch, server.server_address[1])
        mail.send_learning_notification("member@example.invalid", self_test=self_test)
        assert len(server.messages) == 1
        message = BytesParser(policy=policy.default).parsebytes(server.messages[0])
        assert message["To"] == "member@example.invalid"
        assert message.get_content_type() == "text/plain"
        content = message.get_content()
        assert "http://localhost:3000/account#learning" in content
        assert "SMTP接受仅表示邮件服务器接收，不证明已进入收件箱" in content
        assert "verify=" not in content and "a" * 43 not in content
        assert ("自测" in str(message["Subject"])) is self_test


def test_real_smtp_learning_starttls_does_not_downgrade(monkeypatch):
    with sink() as server:
        configure(monkeypatch, server.server_address[1], smtp_security="starttls")
        with pytest.raises(mail.MailDeliveryError, match="^MAIL_DELIVERY_FAILED$"):
            mail.send_learning_notification("member@example.invalid", self_test=True)
        assert not server.messages


def test_notification_template_rejects_invalid_origin_before_smtp(monkeypatch):
    with sink() as server:
        configure(
            monkeypatch, server.server_address[1], public_app_url="http://user:secret@localhost"
        )
        with pytest.raises(mail.MailDeliveryError):
            mail.send_learning_notification("member@example.invalid", self_test=True)
        assert not server.commands
