"""Route control tests only; provider and durable phases are isolated substitutes.

These do not establish real inference, database persistence or live fallback.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.integrations.ai import Generation, ProviderError
from app.modules.ai import routes
from app.modules.ai.service import InvocationConfig
from fastapi import HTTPException
from pydantic import SecretStr


@pytest.fixture
def invocation_route(monkeypatch):
    local_id, cloud_id = uuid4(), uuid4()
    session = Mock()
    access = SimpleNamespace(
        actor=SimpleNamespace(
            user=SimpleNamespace(id=uuid4()), token_hash="isolated-token-hash", session=session
        ),
        tenant=SimpleNamespace(id=uuid4()),
        role="owner",
    )
    configs = {
        local_id: InvocationConfig("ollama", "local-test", "authorized-name", SecretStr("")),
        cloud_id: InvocationConfig(
            "deepseek", "cloud-test", None, SecretStr("test-key-never-sent")
        ),
    }
    choices = Mock(return_value=[(local_id, "ollama"), (cloud_id, "deepseek")])
    reserve = Mock(side_effect=lambda _identity, _tenant, provider_id, *_: configs[provider_id])
    finish = Mock()
    generators = {
        "local-test": AsyncMock(return_value=Generation("local-output", 3, 2)),
        "cloud-test": AsyncMock(return_value=Generation("cloud-output", 5, 4)),
    }
    factory = Mock(side_effect=lambda model, *_: SimpleNamespace(generate=generators[model]))
    monkeypatch.setattr(routes, "provider_choices", choices)
    monkeypatch.setattr(routes, "reserve_attempt", reserve)
    monkeypatch.setattr(routes, "finish_attempt", finish)
    for name in ("OllamaProvider", "DeepSeekProvider", "OpenAIProvider"):
        monkeypatch.setattr(routes, name, factory)
    return SimpleNamespace(
        access=access,
        local_id=local_id,
        cloud_id=cloud_id,
        configs=configs,
        choices=choices,
        reserve=reserve,
        finish=finish,
        factory=factory,
        generators=generators,
    )


def invoke(harness, *, consent=True):
    return asyncio.run(
        routes.test_connection(
            routes.Invocation(prompt="private-test-prompt", consent=consent), harness.access
        )
    )


def test_definite_failure_reserves_and_finishes_each_attempt(invocation_route):
    harness = invocation_route
    harness.generators["local-test"].side_effect = ProviderError("PROVIDER_CONNECT_FAILED")

    response = invoke(harness)["data"]

    assert response == {
        "text": "cloud-output",
        "provider_id": str(harness.cloud_id),
        "input_tokens": 5,
        "output_tokens": 4,
        "cost_usd": None,
        "fallback_errors": [
            {"provider_id": str(harness.local_id), "code": "PROVIDER_CONNECT_FAILED"}
        ],
    }
    harness.access.actor.session.rollback.assert_called_once_with()
    assert harness.reserve.call_count == harness.finish.call_count == 2
    reservations = [call.args for call in harness.reserve.call_args_list]
    identity = reservations[0][0]
    assert identity.user_id == harness.access.actor.user.id
    assert identity.token_hash == harness.access.actor.token_hash
    assert [call[1] for call in reservations] == [harness.access.tenant.id] * 2
    assert [call[2] for call in reservations] == [harness.local_id, harness.cloud_id]
    assert [call[4] for call in reservations] == ["ollama", "ollama"]
    assert reservations[0][3] != reservations[1][3]
    completions = [call.args for call in harness.finish.call_args_list]
    assert completions[0] == (
        identity,
        harness.access.tenant.id,
        reservations[0][3],
        None,
        "PROVIDER_CONNECT_FAILED",
        False,
    )
    assert completions[1] == (
        identity,
        harness.access.tenant.id,
        reservations[1][3],
        Generation("cloud-output", 5, 4),
        None,
        False,
    )
    for generator in harness.generators.values():
        generator.assert_awaited_once_with("private-test-prompt")


def test_unknown_outcome_finishes_as_unknown_and_stops_fallback(invocation_route):
    harness = invocation_route
    harness.generators["local-test"].side_effect = ProviderError("PROVIDER_OUTCOME_UNKNOWN", True)

    with pytest.raises(HTTPException) as error:
        invoke(harness)

    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "MODEL_OUTCOME_UNKNOWN",
        "attempts": [{"provider_id": str(harness.local_id), "code": "PROVIDER_OUTCOME_UNKNOWN"}],
    }
    assert harness.reserve.call_count == harness.finish.call_count == 1
    assert harness.finish.call_args.args[3:] == (None, "PROVIDER_OUTCOME_UNKNOWN", True)
    harness.generators["cloud-test"].assert_not_awaited()


def test_two_definite_failures_return_only_safe_attempt_fields(invocation_route):
    harness = invocation_route
    for generator in harness.generators.values():
        failure = ProviderError("PROVIDER_HTTP_ERROR")
        failure.args = ("private-provider-body-with-test-key-never-sent",)
        generator.side_effect = failure

    with pytest.raises(HTTPException) as error:
        invoke(harness)

    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "ALL_PROVIDERS_UNAVAILABLE",
        "attempts": [
            {"provider_id": str(harness.local_id), "code": "PROVIDER_HTTP_ERROR"},
            {"provider_id": str(harness.cloud_id), "code": "PROVIDER_HTTP_ERROR"},
        ],
    }
    serialized = json.dumps(error.value.detail)
    for private_value in (
        "private-test-prompt",
        "private-provider-body",
        "test-key-never-sent",
        "authorized-name",
    ):
        assert private_value not in serialized
    assert harness.reserve.call_count == harness.finish.call_count == 2
    for completion in harness.finish.call_args_list:
        assert completion.args[3:] == (None, "PROVIDER_HTTP_ERROR", False)


def test_revoked_fallback_authorization_aborts_before_external_request(invocation_route):
    harness = invocation_route
    harness.generators["local-test"].side_effect = ProviderError("PROVIDER_CONNECT_FAILED")
    harness.reserve.side_effect = [
        harness.configs[harness.local_id],
        HTTPException(403, detail={"code": "FORBIDDEN"}),
    ]

    with pytest.raises(HTTPException) as error:
        invoke(harness)

    assert error.value.status_code == 403
    assert harness.reserve.call_count == 2
    assert harness.finish.call_count == 1
    harness.generators["cloud-test"].assert_not_awaited()
    assert harness.factory.call_count == 1


@pytest.mark.parametrize("provider_fails", [False, True])
def test_completion_authorization_failure_never_triggers_fallback(invocation_route, provider_fails):
    harness = invocation_route
    if provider_fails:
        harness.generators["local-test"].side_effect = ProviderError("PROVIDER_HTTP_ERROR")
    harness.finish.side_effect = HTTPException(403, detail={"code": "FORBIDDEN"})

    with pytest.raises(HTTPException) as error:
        invoke(harness)

    assert error.value.status_code == 403
    assert harness.reserve.call_count == harness.finish.call_count == 1
    harness.generators["cloud-test"].assert_not_awaited()


def test_disabled_after_selection_skips_external_request(invocation_route):
    harness = invocation_route
    harness.reserve.side_effect = [None, harness.configs[harness.cloud_id]]

    response = invoke(harness)["data"]

    assert response["provider_id"] == str(harness.cloud_id)
    assert response["fallback_errors"] == []
    harness.generators["local-test"].assert_not_awaited()
    harness.generators["cloud-test"].assert_awaited_once()
    assert harness.reserve.call_count == 2
    assert harness.finish.call_count == 1


@pytest.mark.parametrize("denial", ["consent", "role"])
def test_no_provider_selection_without_admin_and_explicit_consent(invocation_route, denial):
    harness = invocation_route
    if denial == "role":
        harness.access.role = "member"

    with pytest.raises(HTTPException) as error:
        invoke(harness, consent=denial != "consent")

    assert error.value.status_code == (403 if denial == "role" else 400)
    harness.choices.assert_not_called()
    harness.reserve.assert_not_called()
    harness.finish.assert_not_called()
    harness.factory.assert_not_called()
