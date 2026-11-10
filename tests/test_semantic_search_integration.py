"""Real CPU embeddings + pgvector/API/RLS. No fake vectors or external inference."""

import asyncio
import os
from uuid import UUID

import pytest
from app.db.session import get_engine
from app.integrations.embedding import MODEL
from app.modules.notes import semantic
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_organization_integration import (
    auth,
    context,
    endpoint,
    identity,
    organization_accounts,
    protocol_revision,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
    pytest.mark.skipif(
        not os.getenv("LOCAL_EMBEDDING_ENDPOINT"), reason="Real pinned embedding service required"
    ),
]


@pytest.fixture(scope="module")
def semantic_accounts():
    yield from organization_accounts.__wrapped__()


def index(account, note):
    return asyncio.run(
        semantic.index_revision(
            identity(account),
            UUID(account["tenant_id"]),
            semantic.IndexInput(
                note_id=note["note_id"],
                revision_id=note["revision_id"],
                consent=True,
            ),
        )
    )


def test_live_cpu_semantics_exact_quotes_version_invalidation_and_idempotency(semantic_accounts):
    live, admin, (owner, other) = semantic_accounts
    first, unrelated = owner["notes"]
    first = {
        **first,
        **protocol_revision(
            owner,
            UUID(str(first["snapshot_id"])),
            "先准备3个材料，再验证输出。",
            "开启双重认证可以防止账户被窃取",
        ),
    }
    unrelated = {
        **unrelated,
        **protocol_revision(
            owner,
            UUID(str(unrelated["snapshot_id"])),
            "先准备3个材料，再验证输出。",
            "番茄需要阳光和水分",
        ),
    }
    assert index(owner, first)["reused"] is False
    assert index(owner, unrelated)["reused"] is False
    assert index(owner, first)["reused"] is True
    response = live.get(
        endpoint(owner) + "/knowledge/semantic/search",
        headers=auth(owner),
        params={"q": "如何保护账号不被盗用"},
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["coverage"]["indexed_latest_notes"] == 2
    assert result["data"][0]["note_id"] == first["note_id"]
    assert result["data"][0]["similarity"] > 0.35
    assert result["data"][0]["revision_id"] == first["revision_id"]
    assert "双重认证" in result["data"][0]["snippet"]
    assert unrelated["note_id"] not in [r["note_id"] for r in result["data"]]
    with admin.connect() as connection:
        row = connection.execute(
            text(
                "SELECT r.title || E'\n' || r.content_md,c.quote,c.embedding::text "
                "FROM semantic_chunks c JOIN note_revisions r ON r.tenant_id=c.tenant_id "
                "AND r.id=c.revision_id WHERE c.tenant_id=:t AND c.revision_id=:r AND c.position=0"
            ),
            {"t": owner["tenant_id"], "r": first["revision_id"]},
        ).one()
        assert row[1] == row[0][:96]
        assert len(row[2].split(",")) == 384
        before = connection.execute(
            text("SELECT count(*) FROM audit_events WHERE tenant_id=:t AND action='notes.indexed'"),
            {"t": owner["tenant_id"]},
        ).scalar_one()
        assert before == 2
    protocol_revision(
        owner, UUID(str(first["snapshot_id"])), "先准备3个材料，再验证输出。", "新的未索引修订"
    )
    result = live.get(
        endpoint(owner) + "/knowledge/semantic/search",
        headers=auth(owner),
        params={"q": "如何保护账号不被盗用"},
    ).json()["data"]
    assert result["coverage"]["indexed_latest_notes"] == 1
    assert first["note_id"] not in [r["note_id"] for r in result["data"]]
    assert (
        live.get(
            endpoint(owner) + "/knowledge/semantic/search",
            headers=auth(other),
            params={"q": "账号"},
        ).status_code
        == 404
    )


def test_live_index_boundaries_rls_exact_text_no_update_or_cross_tenant(semantic_accounts):
    live, _admin, (owner, other) = semantic_accounts
    note = other["notes"][0]
    body = {"note_id": note["note_id"], "revision_id": note["revision_id"], "consent": True}
    path = endpoint(other) + "/knowledge/semantic/index"
    assert live.post(path, json=body).status_code == 401
    assert live.post(path, headers=auth(owner), json=body).status_code == 404
    assert live.post(path, headers=auth(other), json={**body, "consent": False}).status_code == 422
    assert (
        live.post(
            path, headers=auth(other), json={**body, "tenant_id": owner["tenant_id"]}
        ).status_code
        == 422
    )
    assert live.post(path + "?tenant_id=x", headers=auth(other), json=body).status_code == 422
    assert index(other, note)["chunks"] > 0
    with get_engine().begin() as connection:
        context(connection, owner)
        assert (
            connection.execute(
                text("SELECT count(*) FROM semantic_chunks WHERE tenant_id=:t"),
                {"t": other["tenant_id"]},
            ).scalar_one()
            == 0
        )
    for command in (
        "UPDATE semantic_chunks SET quote='forged' WHERE tenant_id=:t",
        "DELETE FROM semantic_chunks WHERE tenant_id=:t",
    ):
        with pytest.raises(DBAPIError), get_engine().begin() as connection:
            context(connection, other)
            connection.execute(text(command), {"t": other["tenant_id"]})
    with pytest.raises(DBAPIError), get_engine().begin() as connection:
        context(connection, other)
        connection.execute(
            text(
                "INSERT INTO semantic_chunks(tenant_id,note_id,snapshot_id,revision_id,position,"
                "model,quote,embedding,created_by) "
                "SELECT tenant_id,note_id,snapshot_id,revision_id,"
                "127,model,'forged',embedding,created_by FROM semantic_chunks "
                "WHERE tenant_id=:t LIMIT 1"
            ),
            {"t": other["tenant_id"]},
        )
    with pytest.raises(HTTPException) as failure:
        index(owner, note)
    assert failure.value.status_code == 404
    assert (
        MODEL
        in live.get(
            endpoint(other) + "/knowledge/semantic/search",
            headers=auth(other),
            params={"q": "准备材料"},
        ).text
    )


def test_real_embedding_revocation_before_commit_and_audit_failure_are_atomic(
    semantic_accounts, monkeypatch
):
    _live, admin, (owner, other) = semantic_accounts
    tenant = UUID(other["tenant_id"])
    original = other["notes"][0]
    new_note = protocol_revision(
        other, UUID(str(original["snapshot_id"])), "先准备3个材料，再验证输出。", "验证撤权索引"
    )
    actual = semantic.LocalEmbeddingProvider.encode

    async def revoke_after_actual_embedding(self, values):
        vectors = await actual(self, values)
        with admin.begin() as connection:
            connection.execute(
                text("UPDATE tenant_members SET active=false WHERE tenant_id=:t AND user_id=:u"),
                {"t": tenant, "u": other["user_id"]},
            )
        return vectors

    monkeypatch.setattr(semantic.LocalEmbeddingProvider, "encode", revoke_after_actual_embedding)
    try:
        with pytest.raises(HTTPException) as failure:
            index(other, new_note)
        assert failure.value.status_code == 404
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM semantic_chunks WHERE tenant_id=:t AND revision_id=:r"
                    ),
                    {"t": tenant, "r": new_note["revision_id"]},
                ).scalar_one()
                == 0
            )
    finally:
        with admin.begin() as connection:
            connection.execute(
                text("UPDATE tenant_members SET active=true WHERE tenant_id=:t AND user_id=:u"),
                {"t": tenant, "u": other["user_id"]},
            )
        monkeypatch.setattr(semantic.LocalEmbeddingProvider, "encode", actual)

    def fail_audit(*_args):
        raise RuntimeError("Protocol audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(semantic, "record", fail_audit)
        with pytest.raises(RuntimeError, match="Protocol audit failure"):
            index(other, new_note)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM semantic_chunks WHERE tenant_id=:t AND revision_id=:r"),
                {"t": tenant, "r": new_note["revision_id"]},
            ).scalar_one()
            == 0
        )
    with admin.begin() as connection:
        connection.execute(
            text("UPDATE tenant_members SET role='member' WHERE tenant_id=:t AND user_id=:u"),
            {"t": tenant, "u": other["user_id"]},
        )
    try:
        with pytest.raises(HTTPException) as failure:
            index(other, new_note)
        assert failure.value.status_code == 403
    finally:
        with admin.begin() as connection:
            connection.execute(
                text("UPDATE tenant_members SET role='owner' WHERE tenant_id=:t AND user_id=:u"),
                {"t": tenant, "u": other["user_id"]},
            )
