"""Real personal analysis counters and membership boundaries."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from test_organization_integration import auth, endpoint
from test_organization_integration import organization_accounts as accounts_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def analysis_accounts():
    yield from accounts_fixture.__wrapped__()


def test_analysis_counts_real_completion_windows_and_personal_isolation(analysis_accounts):
    live, admin, (owner, other) = analysis_accounts
    url = endpoint(owner) + "/learning/analysis"
    initial = live.get(url, headers=auth(owner))
    assert initial.status_code == 200
    data = initial.json()["data"]
    assert data["total_notes"] == 2
    assert data["completed_notes"] == data["completed_last_7_days"] == 0
    assert data["completed_last_30_days"] == 0

    for note, state in zip(owner["notes"], ("seen", "skimmed"), strict=True):
        response = live.put(
            endpoint(owner) + f"/notes/{note['note_id']}/learning",
            headers=auth(owner),
            json={"status": state, "reminder_at": None},
        )
        assert response.status_code == 200
    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE note_learning SET completed_at=:old WHERE tenant_id=:tenant "
                "AND note_id=:note"
            ),
            {
                "old": datetime.now(UTC) - timedelta(days=8),
                "tenant": owner["tenant_id"],
                "note": owner["notes"][0]["note_id"],
            },
        )
    result = live.get(url, headers=auth(owner))
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["completed_notes"] == data["completed_last_30_days"] == 2
    assert data["completed_last_7_days"] == 1
    assert data["states"] == {"unseen": 0, "seen": 1, "skimmed": 1, "deep_read": 0}
    assert data["reminders"] == {"due": 0, "upcoming": 0}
    assert datetime.fromisoformat(data["as_of"]).utcoffset() == timedelta(0)
    assert result.headers["cache-control"] == "no-store"

    other_result = live.get(endpoint(other) + "/learning/analysis", headers=auth(other))
    assert other_result.status_code == 200
    assert other_result.json()["data"]["completed_notes"] == 0


def test_analysis_revoke_blocks_access_without_writes(analysis_accounts):
    live, _admin, (owner, other) = analysis_accounts
    members = endpoint(owner) + "/members"
    response = live.post(
        members,
        headers=auth(owner),
        json={"user_id": other["user_id"], "role": "member"},
    )
    assert response.status_code == 201
    owner_url = endpoint(owner) + "/learning/analysis"
    assert live.get(owner_url, headers=auth(other)).status_code == 200
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(owner_url, headers=auth(other)).status_code == 404
