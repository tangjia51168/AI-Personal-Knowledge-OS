"""Evidence-first research never fabricates an answer or crosses the model boundary."""

from types import SimpleNamespace
from uuid import uuid4

from app.modules.notes import research
from app.modules.notes.search import SearchQuery


def test_research_returns_candidates_and_explicitly_empty_answer(monkeypatch):
    user_id = uuid4()
    access = SimpleNamespace(
        actor=SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            token_hash="token-hash",
            session=object(),
        )
    )

    monkeypatch.setattr(
        research,
        "search_page",
        lambda _access, _params: {
            "data": [
                {
                    "note_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "title": "证据笔记",
                    "version": 1,
                    "origin": "user_edit",
                    "matched_field": "body",
                    "snippet": "原文片段",
                }
            ],
            "next_cursor": None,
        },
    )
    monkeypatch.setattr(research, "recheck_identity", lambda *_args: None)
    result = research.research_page(
        access,
        SearchQuery.model_validate({"q": "原文", "limit": 10}),
    )["data"]
    assert result["answer"] is None
    assert result["answer_status"] == "evidence_ready"
    assert result["mode"] == "evidence_retrieval"
    assert result["sources"][0]["snippet"] == "原文片段"


def test_research_marks_missing_material_as_insufficient(monkeypatch):
    access = SimpleNamespace(
        actor=SimpleNamespace(
            user=SimpleNamespace(id=uuid4()), token_hash="token-hash", session=object()
        )
    )
    monkeypatch.setattr(
        research,
        "search_page",
        lambda *_args: {"data": [], "next_cursor": None},
    )
    monkeypatch.setattr(research, "recheck_identity", lambda *_args: None)
    result = research.research_page(
        access,
        SearchQuery.model_validate({"q": "未知", "limit": 10}),
    )["data"]
    assert result["answer"] is None
    assert result["answer_status"] == "insufficient_evidence"
