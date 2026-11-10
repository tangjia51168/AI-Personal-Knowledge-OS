"""Real saved-content search, fixed revision navigation and bounded space graphs."""

import os
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.identity.access import authenticate_session, member_access
from app.modules.notes import space_graph
from app.modules.organization.content import LabelUpdateInput
from app.modules.organization.service import update_label
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_annotations_integration import create as annotate
from test_annotations_integration import payload
from test_note_links_integration import set_link
from test_organization_integration import (
    auth,
    context,
    create,
    endpoint,
    identity,
    organization_accounts,
    protocol_note,
    protocol_revision,
    set_label,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def explorer_accounts():
    yield from organization_accounts.__wrapped__()


def search(live, account, q, **params):
    response = live.get(
        endpoint(account) + "/knowledge/search", headers=auth(account), params={"q": q, **params}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_explorer_search_scopes_filters_latest_versions_and_literal_text(explorer_accounts):
    live, _admin, (owner, other) = explorer_accounts
    note, second = owner["notes"]
    annotate(owner, note, payload(body="独有批注 %_!\\ 😀"))
    assert (
        search(live, owner, "%_!\\", scope="annotation")["data"][0]["revision_id"]
        == note["revision_id"]
    )
    assert search(live, other, "独有批注")["data"] == []
    assert search(live, owner, "不存在的独有词")["data"] == []
    assert (
        search(live, owner, "分类协议0甲", scope="title")["data"][0]["note_id"] == note["note_id"]
    )
    for scope in ("body", "evidence"):
        hits = search(live, owner, "准备3个材料", scope=scope)["data"]
        assert {row["note_id"] for row in hits} == {note["note_id"], second["note_id"]}
        assert all(
            row["matched_field"] == scope and "准备3个材料" in row["snippet"] for row in hits
        )
    assert search(live, owner, "准备3个材料", scope="title")["data"] == []
    label = create(owner, "检索归档分类")
    set_label(owner, note, label)
    update_label(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(label["id"]),
        LabelUpdateInput(expected_version=1, active=False),
    )
    filtered = search(
        live, owner, "准备", label_id=label["id"], platform="web", origin="ai_generated"
    )
    assert [row["note_id"] for row in filtered["data"]] == [note["note_id"]]
    assert search(live, owner, "准备", label_id=str(uuid4()))["data"] == []
    assert search(live, owner, "准备", origin="user_edit")["data"] == []
    hit = search(live, owner, "独有批注", scope="annotation")["data"][0]
    newer = protocol_revision(owner, note["snapshot_id"], note["source_text"], "新修订检索协议")
    assert newer["revision_id"] != hit["revision_id"]
    assert search(live, owner, "独有批注", scope="annotation")["data"] == []
    assert search(live, owner, "分类协议0甲", scope="title")["data"] == []
    saved = live.get(
        endpoint(owner) + f"/notes/{hit['note_id']}/revisions/{hit['revision_id']}",
        headers=auth(owner),
    )
    assert saved.status_code == 200 and saved.json()["data"]["id"] == hit["revision_id"]
    assert search(live, owner, "新修订检索协议")["data"][0]["revision_id"] == newer["revision_id"]


def test_explorer_pagination_stale_anchor_and_identity_boundaries(explorer_accounts):
    live, _admin, (owner, other) = explorer_accounts
    url = endpoint(owner) + "/knowledge/search"
    assert live.get(url, params={"q": "准备"}).status_code == 401
    assert (
        live.get(
            url, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}, params={"q": "准备"}
        ).status_code
        == 404
    )
    first = search(live, owner, "准备", limit=1)
    assert len(first["data"]) == 1 and first["next_cursor"]
    second = search(live, owner, "准备", limit=1, cursor=first["next_cursor"])
    assert len(second["data"]) == 1 and second["next_cursor"] is None
    assert first["data"][0]["note_id"] != second["data"][0]["note_id"]
    for cursor in (str(uuid4()), other["notes"][0]["note_id"]):
        denied = live.get(url, headers=auth(owner), params={"q": "准备", "cursor": cursor})
        assert denied.status_code == 404
    for params in (
        {},
        {"q": ""},
        {"q": "x", "scope": "history"},
        {"q": "x", "limit": 51},
        {"q": "x", "user_id": other["user_id"]},
    ):
        assert live.get(url, headers=auth(owner), params=params).status_code == 422
    assert live.post(url, headers=auth(owner), json={"q": "准备"}).status_code == 405


def test_space_graph_real_links_isolated_notes_read_only_and_caps(explorer_accounts, monkeypatch):
    live, admin, (owner, other) = explorer_accounts
    a, b = owner["notes"]
    isolated = protocol_note(owner, "空间孤立节点")
    set_link(owner, a, b)
    url = endpoint(owner) + "/knowledge/graph"
    assert live.get(url).status_code == 401
    assert live.get(url, headers=auth(other)).status_code == 404
    assert live.post(url, headers=auth(owner)).status_code == 405
    assert live.get(url + "?limit=999", headers=auth(owner)).status_code == 422
    with admin.connect() as connection:
        before = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            for table in ("audit_events", "note_revisions", "note_links")
        ]
    graph = live.get(url, headers=auth(owner)).json()["data"]
    assert {node["id"] for node in graph["nodes"]} == {
        a["note_id"],
        b["note_id"],
        isolated["note_id"],
    }
    assert len(graph["edges"]) == 1 and not graph["truncated"]
    assert graph["edges"][0]["method"] == "manual" and graph["edges"][0]["confidence"] is None
    assert all(
        edge["source"] != isolated["note_id"] and edge["target"] != isolated["note_id"]
        for edge in graph["edges"]
    )
    with Session(get_engine()) as session, session.begin():
        access = member_access(
            UUID(owner["tenant_id"]), authenticate_session(session, owner["access_token"])
        )
        with monkeypatch.context() as patch:
            patch.setattr(space_graph, "MAX_GRAPH_NODES", 2)
            capped = space_graph.space_graph(access)
        assert len(capped["nodes"]) == 2 and capped["truncation_reasons"] == ["node_limit"]
        with monkeypatch.context() as patch:
            patch.setattr(space_graph, "MAX_GRAPH_EDGES", 0)
            capped = space_graph.space_graph(access)
        assert capped["edges"] == [] and capped["truncation_reasons"] == ["edge_limit"]
    with admin.connect() as connection:
        after = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            for table in ("audit_events", "note_revisions", "note_links")
        ]
    assert before == after
    set_link(owner, a, b, active=False)
    assert live.get(url, headers=auth(owner)).json()["data"]["edges"] == []


def test_explorer_rls_and_revoked_members_cannot_read(explorer_accounts):
    live, _admin, (owner, other) = explorer_accounts
    with get_engine().begin() as connection:
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_revisions WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_annotations WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    for path in ("/knowledge/search?q=准备", "/knowledge/graph"):
        assert live.get(endpoint(owner) + path, headers=auth(other)).status_code == 200
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    for path in ("/knowledge/search?q=准备", "/knowledge/graph"):
        assert live.get(endpoint(owner) + path, headers=auth(other)).status_code == 404
