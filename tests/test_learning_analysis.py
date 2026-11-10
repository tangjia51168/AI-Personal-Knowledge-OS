"""Analysis wire contract and tenant/member-bound aggregate guards."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.modules.learning import analysis
from app.modules.learning.analysis import LearningAnalysis
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql


def payload():
    return {
        "as_of": datetime.now(UTC),
        "total_notes": 4,
        "recorded_notes": 3,
        "unrecorded_notes": 1,
        "states": {"unseen": 1, "seen": 1, "skimmed": 1, "deep_read": 1},
        "completed_notes": 3,
        "completed_last_7_days": 2,
        "completed_last_30_days": 3,
        "reminders": {"due": 1, "upcoming": 1},
    }


def test_analysis_contract_has_bounded_time_windows_without_event_history():
    data = LearningAnalysis.model_validate(payload())
    assert data.completed_last_7_days == 2
    assert "review_count" not in data.model_dump()
    for change in (
        {"completed_notes": 4},
        {"completed_last_7_days": 4},
        {"completed_last_30_days": 4},
        {"private_content": "forbidden"},
    ):
        with pytest.raises(ValidationError):
            LearningAnalysis.model_validate({**payload(), **change})


def test_analysis_is_one_personal_tenant_bound_outer_aggregate(monkeypatch):
    session = Mock()
    session.execute.return_value.one.return_value = SimpleNamespace(
        total=4,
        recorded=3,
        unseen=1,
        seen=1,
        skimmed=1,
        deep_read=1,
        completed=3,
        completed_7=2,
        completed_30=3,
        due=1,
        upcoming=1,
    )
    access = SimpleNamespace(
        actor=SimpleNamespace(session=session, user=SimpleNamespace(id=uuid4()), token_hash="hash"),
        tenant=SimpleNamespace(id=uuid4()),
    )
    recheck = Mock()
    monkeypatch.setattr(analysis, "recheck_identity", recheck)
    data = analysis.analyze_learning(access)
    assert data.completed_notes == 3
    recheck.assert_called_once()
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN note_learning" in sql
    assert "note_learning.user_id =" in sql
    assert "note_learning.tenant_id = notes.tenant_id" in sql
    assert "WHERE notes.tenant_id =" in sql
    assert "INSERT" not in sql and "UPDATE" not in sql
