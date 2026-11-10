"""Search input contract and literal SQL behavior; real services tested separately."""

from uuid import uuid4

import pytest
from app.modules.identity.access import member_access
from app.modules.notes import routes
from app.modules.notes.search import SearchQuery
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"q": ""},
        {"q": "  "},
        {"q": "x\x00y"},
        {"q": "x" * 121},
        {"q": "x", "scope": "history"},
        {"q": "x", "user_id": str(uuid4())},
        {"q": "x", "limit": 51},
        {"q": "x", "platform": "unknown"},
    ],
)
def test_search_rejects_empty_unbounded_and_unknown_queries(data):
    with pytest.raises(ValidationError):
        SearchQuery.model_validate(data)


def test_search_preserves_literal_wildcards_and_unicode():
    assert SearchQuery(q="  %_!\\  ").q == "%_!\\"
    assert SearchQuery(q="😀" * 120).q == "😀" * 120


def test_explorer_authentication_and_query_boundaries(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    base = f"/api/v1/spaces/{uuid4()}/knowledge"
    for path in ("/search?q=test", "/graph"):
        assert client.get(base + path).status_code == 401
        assert client.post(base + path).status_code == 405
    app.dependency_overrides[member_access] = lambda: object()
    monkeypatch.setattr(routes, "search_page", lambda _access, params: {"q": params.q})
    monkeypatch.setattr(routes, "space_graph", lambda _access: {"nodes": [], "edges": []})
    assert client.get(base + "/search", params={"q": " 测试 "}).json() == {"q": "测试"}
    assert client.get(base + "/search").status_code == 422
    assert client.get(base + "/search?q=test&tenant_id=x").status_code == 422
    assert client.get(base + "/graph?scope=all").status_code == 422
    assert client.get(base + "/graph").status_code == 200
