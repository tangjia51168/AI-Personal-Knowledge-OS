"""HTTP parameter extraction checks; real filtering is covered by live DB tests."""

from uuid import uuid4

import pytest
from app.modules.identity.access import member_access
from app.modules.notes import routes as note_routes
from app.modules.sources import routes as source_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("routes", "suffix", "kind", "value"),
    [
        (source_routes, "sources", "status", "accepted"),
        (note_routes, "notes", "origin", "user_edit"),
    ],
)
def test_content_list_http_extracts_validated_filters(routes, suffix, kind, value, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    access = object()
    app.dependency_overrides[member_access] = lambda: access
    calls = []

    def capture(actual_access, params):
        assert actual_access is access
        calls.append(params)
        return {"data": [], "next_cursor": None}

    monkeypatch.setattr(routes, "list_page", capture)
    client = TestClient(app)
    path = f"/api/v1/spaces/{uuid4()}/{suffix}"
    cursor = uuid4()
    response = client.get(
        path,
        params={
            "q": "  %_\\tail!  ",
            "platform": "bilibili",
            "cursor": str(cursor),
            "limit": 7,
            kind: value,
        },
    )
    assert response.status_code == 200 and response.json() == {"data": [], "next_cursor": None}
    assert calls[-1].q == "%_\\tail!" and calls[-1].cursor == cursor
    assert calls[-1].platform == "bilibili" and calls[-1].limit == 7
    assert getattr(calls[-1], kind) == value
    assert client.get(path).status_code == 200
    assert calls[-1].q == "" and calls[-1].limit == 20 and calls[-1].cursor is None
    before = len(calls)
    for params in (
        {"q": "x" * 121},
        {"limit": 0},
        {"limit": 51},
        {"cursor": "invalid"},
        {"platform": "unknown"},
        {kind: "unknown"},
    ):
        assert client.get(path, params=params).status_code == 422
    assert len(calls) == before
    assert client.get(path, params={"q": "  " + "😀" * 120 + "  "}).status_code == 200
    assert len(calls[-1].q.encode("utf-8")) == 480


def test_personal_note_filters_are_validated_and_cannot_choose_another_user(monkeypatch):
    app = FastAPI()
    app.include_router(note_routes.router)
    app.dependency_overrides[member_access] = lambda: object()
    calls = []

    def capture(_access, params):
        calls.append(params)
        return {"data": [], "next_cursor": None}

    monkeypatch.setattr(note_routes, "list_page", capture)
    client = TestClient(app)
    path = f"/api/v1/spaces/{uuid4()}/notes"
    for state in ("unseen", "seen", "skimmed", "deep_read", "unrecorded"):
        assert (
            client.get(path, params={"reading_status": state, "review": "due"}).status_code == 200
        )
        assert calls[-1].reading_status == state and calls[-1].review == "due"
    assert client.get(path, params={"review": "upcoming"}).status_code == 200
    for params in ({"reading_status": "all"}, {"review": "false"}, {"user_id": str(uuid4())}):
        assert client.get(path, params=params).status_code == 422
