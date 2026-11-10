"""Real tenant-authorized manual-link local graphs, without inference or writes."""

import os
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.identity.access import authorized_transaction
from app.modules.notes import graph
from fastapi import HTTPException
from sqlalchemy import event, text
from test_note_links_integration import set_link
from test_organization_integration import (
    auth,
    context,
    endpoint,
    identity,
    organization_accounts,
    protocol_note,
    protocol_revision,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def graph_accounts():
    yield from organization_accounts.__wrapped__()


def path(account, note):
    return endpoint(account) + f"/notes/{note['note_id']}/graph"


def test_real_graph_identity_scope_depth_and_read_only_method(graph_accounts):
    live, _admin, (owner, other) = graph_accounts
    note = owner["notes"][0]
    url = path(owner, note)
    assert live.get(url).status_code == 401
    assert (
        live.get(url, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code == 404
    )
    assert live.get(path(other, note), headers=auth(other)).status_code == 404
    assert (
        live.get(url.replace(note["note_id"], str(uuid4())), headers=auth(owner)).status_code == 404
    )
    for depth in ("0", "3", "bad"):
        assert live.get(url, params={"depth": depth}, headers=auth(owner)).status_code == 422
    assert live.post(url, headers=auth(owner)).status_code == 405
    response = live.get(url, headers=auth(owner))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    data = response.json()["data"]
    assert data["depth"] == 1 and data["edges"] == [] and not data["truncated"]
    assert data["nodes"][0]["id"] == note["note_id"]


def test_real_graph_hops_latest_titles_inactive_and_disconnected_edges(graph_accounts):
    live, admin, (owner, other) = graph_accounts
    notes = [protocol_note(owner, f"图谱协议{index}") for index in range(6)]
    root, a, b, c, disconnected, inactive = notes
    for left, right in ((root, a), (root, b), (a, b), (b, c), (c, disconnected), (root, inactive)):
        set_link(owner, left, right)
    set_link(owner, root, inactive, active=False)
    set_link(other, other["notes"][0], protocol_note(other, "外租户图谱"))
    newer = protocol_revision(owner, c["snapshot_id"], c["source_text"], "图谱新标题")
    params = {"tenant": owner["tenant_id"]}
    tables = (
        "note_revisions",
        "note_links",
        "note_runs",
        "audit_events",
        "ai_usage",
        "processing_jobs",
    )

    def counts():
        with admin.connect() as connection:
            return [
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), params
                ).scalar_one()
                for table in tables
            ]

    before = counts()
    one = live.get(path(owner, root), headers=auth(owner)).json()["data"]
    two = live.get(path(owner, root), params={"depth": 2}, headers=auth(owner)).json()["data"]
    assert {node["id"] for node in one["nodes"]} == {item["note_id"] for item in (root, a, b)}
    assert len(one["edges"]) == 3
    assert {node["id"]: node["distance"] for node in two["nodes"]} == {
        root["note_id"]: 0,
        a["note_id"]: 1,
        b["note_id"]: 1,
        c["note_id"]: 2,
    }
    assert len(two["edges"]) == 4 and not two["truncated"]
    title = next(node for node in two["nodes"] if node["id"] == c["note_id"])
    latest = live.get(endpoint(owner) + f"/notes/{c['note_id']}", headers=auth(owner)).json()[
        "data"
    ]["revision"]
    assert latest["id"] == newer["revision_id"]
    assert (
        title["title"] == "用户导入笔记：图谱新标题" and title["version"] == latest["version"] == 2
    )
    assert all(
        edge["method"] == "manual"
        and edge["confidence"] is None
        and edge["created_by"] == owner["user_id"]
        and edge["updated_at"]
        for edge in two["edges"]
    )
    assert (
        two == live.get(path(owner, root), params={"depth": 2}, headers=auth(owner)).json()["data"]
    )
    assert counts() == before


def test_real_graph_bounded_queries_inventory_guard_and_rls(graph_accounts, monkeypatch):
    _live, _admin, (owner, other) = graph_accounts
    root, target = owner["notes"]
    set_link(owner, root, target)
    statements = []

    def observed(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement)

    with authorized_transaction(identity(owner), UUID(owner["tenant_id"])) as (session, _role):
        event.listen(session.connection(), "before_cursor_execute", observed)
        data = graph.local_graph(session, UUID(owner["tenant_id"]), UUID(root["note_id"]), 2)
        assert len(statements) == 3 and len(data["nodes"]) == 2
    with monkeypatch.context() as patch:
        patch.setattr(graph, "MAX_SPACE_LINKS", 0)
        with authorized_transaction(identity(owner), UUID(owner["tenant_id"])) as (session, _role):
            with pytest.raises(HTTPException) as denied:
                graph.local_graph(session, UUID(owner["tenant_id"]), UUID(root["note_id"]), 1)
            assert denied.value.detail["code"] == "GRAPH_INVENTORY_LIMIT"
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM note_links")).scalar_one() == 0
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_links WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )


def test_real_graph_truncation_is_explicit_and_keeps_selected_nodes_connected(
    graph_accounts, monkeypatch
):
    _live, _admin, (owner, _other) = graph_accounts
    notes = [protocol_note(owner, f"截断图谱{index}") for index in range(5)]
    root = notes[0]
    for left_index, left in enumerate(notes):
        for right in notes[left_index + 1 :]:
            set_link(owner, left, right)
    with monkeypatch.context() as patch:
        patch.setattr(graph, "MAX_GRAPH_NODES", 4)
        patch.setattr(graph, "MAX_GRAPH_EDGES", 3)
        with authorized_transaction(identity(owner), UUID(owner["tenant_id"])) as (session, _role):
            data = graph.local_graph(session, UUID(owner["tenant_id"]), UUID(root["note_id"]), 2)
    assert len(data["nodes"]) == 4 and len(data["edges"]) == 3
    assert data["truncated"] and data["truncation_reasons"] == ["node_limit", "edge_limit"]
    assert data["limits"] == {"nodes": 4, "edges": 3}
    node_ids = {node["id"] for node in data["nodes"]}
    reached = {root["note_id"]}
    for edge in data["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
        assert edge["source"] in reached or edge["target"] in reached
        reached.update((edge["source"], edge["target"]))
    assert reached == node_ids


def test_real_graph_membership_revocation_and_logout(graph_accounts):
    live, _admin, (owner, other) = graph_accounts
    url = path(owner, owner["notes"][0])
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    assert live.get(url, headers=auth(other)).status_code == 200
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(url, headers=auth(other)).status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert live.get(path(other, other["notes"][0]), headers=auth(other)).status_code == 401
