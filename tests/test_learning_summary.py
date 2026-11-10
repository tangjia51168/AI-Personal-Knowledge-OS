"""Summary wire contract and aggregate query guards; live DB cases are separate."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.modules.learning import summary
from app.modules.learning.summary import LearningSummary
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql


def payload():
    return {
        "as_of": datetime.now(UTC),
        "total_notes": 4,
        "recorded_notes": 3,
        "unrecorded_notes": 1,
        "states": {"unseen": 1, "seen": 1, "skimmed": 1, "deep_read": 1},
        "reminders": {"due": 1, "upcoming": 1},
    }


def test_learning_summary_contract_distinguishes_unrecorded_from_recorded_unseen():
    data = LearningSummary.model_validate(payload())
    assert data.total_notes == 4 and data.unrecorded_notes == 1
    assert set(data.model_dump()) == {
        "as_of",
        "total_notes",
        "recorded_notes",
        "unrecorded_notes",
        "states",
        "reminders",
    }
    empty = payload()
    for field in ("total_notes", "recorded_notes", "unrecorded_notes"):
        empty[field] = 0
    empty["states"] = dict.fromkeys(empty["states"], 0)
    empty["reminders"] = {"due": 0, "upcoming": 0}
    assert LearningSummary.model_validate(empty).total_notes == 0


@pytest.mark.parametrize(
    "change",
    [
        {"total_notes": -1},
        {"total_notes": 5},
        {"recorded_notes": 4},
        {"unrecorded_notes": 2},
        {"reminders": {"due": 2, "upcoming": 1}},
        {"states": {"unseen": 0, "seen": 1, "skimmed": 1, "deep_read": 2}},
        {"as_of": datetime(2026, 11, 8)},
        {"private_content": "forbidden"},
    ],
)
def test_learning_summary_rejects_inconsistent_contract(change):
    with pytest.raises(ValidationError):
        LearningSummary.model_validate({**payload(), **change})


def test_summary_is_one_personal_tenant_bound_outer_aggregate(monkeypatch):
    session = Mock()
    session.execute.return_value.one.return_value = SimpleNamespace(
        total=4, recorded=3, unseen=1, seen=1, skimmed=1, deep_read=1, due=1, upcoming=1
    )
    access = SimpleNamespace(
        actor=SimpleNamespace(session=session, user=SimpleNamespace(id=uuid4()), token_hash="hash"),
        tenant=SimpleNamespace(id=uuid4()),
    )
    recheck = Mock()
    monkeypatch.setattr(summary, "recheck_identity", recheck)
    data = summary.summarize_learning(access)
    recheck.assert_called_once()
    assert data.total_notes == 4
    session.execute.assert_called_once()
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN note_learning" in sql and "FILTER (WHERE" in sql
    assert "note_learning.user_id =" in sql
    assert "note_learning.tenant_id = notes.tenant_id" in sql
    assert "WHERE notes.tenant_id =" in sql
    assert "LIMIT" not in sql and "note_revisions" not in sql
    assert "INSERT" not in sql and "UPDATE" not in sql
