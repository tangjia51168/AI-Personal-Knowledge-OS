"""Real retrieval/RLS/idempotency and empty-answer path; no paid generation."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.notes import research_service as service
from app.modules.notes.research_content import ResearchInput
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_manual_first_note_integration import create, payload
from test_notes_integration import context, identity, knowledge_accounts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@contextmanager
def accounts():
    yield from knowledge_accounts.__wrapped__()


def request(query="材料"):
    return ResearchInput(
        idempotency_key=uuid4(), consent=True, question="需要准备什么材料？", query=query
    )


def test_research_real_retrieval_citations_parallel_replay_and_history(monkeypatch):
    with accounts() as (live, admin, (owner, other)):
        note = create(owner, payload())
        body = request()
        tenant = UUID(owner["tenant_id"])

        def fail(*args):
            raise RuntimeError("controlled research audit failure")

        with monkeypatch.context() as patch:
            patch.setattr(service, "record", fail)
            with pytest.raises(RuntimeError, match="controlled research"):
                service.begin(identity(owner), tenant, body)
        assert service.read_history(identity(owner), tenant) == []
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(
                workers.map(lambda _: service.begin(identity(owner), tenant, body), range(2))
            )
        assert sorted(fresh for _run, fresh in results) == [False, True]
        run = results[0][0]
        assert len(run["citations"]) == 1
        citation = run["citations"][0]
        assert citation["revision_id"] == note["revision_id"]
        assert citation["snapshot_id"] == owner["snapshot_id"]
        assert citation["quote"] == "先准备3个材料，再验证输出。"
        # Even the application role cannot append a forged quote/title/version.
        for column, value in (("quote", "forged"), ("title", "forged"), ("version", 999)):
            with pytest.raises(DBAPIError), get_engine().begin() as conn:
                context(conn, owner)
                conn.execute(
                    text(
                        "INSERT INTO research_citations SELECT tenant_id,run_id,'R002',"
                        "note_id,revision_id,snapshot_id,evidence_key,"
                        + (":value" if column == "version" else "version")
                        + ","
                        + (":value" if column == "title" else "title")
                        + ",provenance,content_kind,"
                        + (":value" if column == "quote" else "quote")
                        + " FROM research_citations WHERE run_id=:id"
                    ),
                    {"id": run["id"], "value": value},
                )
        with pytest.raises(HTTPException) as error:
            service.begin(identity(owner), tenant, request())
        assert error.value.detail["code"] == "RESEARCH_OUTCOME_UNRESOLVED"
        with monkeypatch.context() as patch:
            patch.setattr(service, "record", fail)
            with pytest.raises(RuntimeError, match="controlled research"):
                service.finish(identity(owner), tenant, UUID(run["id"]), "failed", code="PROTOCOL")
        assert service.read_history(identity(owner), tenant, UUID(run["id"]))["status"] == "pending"
        finished = service.finish(
            identity(owner), tenant, UUID(run["id"]), "unknown", code="PROTOCOL_NO_MODEL"
        )
        assert finished["answer"] is None and service.begin(identity(owner), tenant, body) == (
            finished,
            False,
        )
        root = f"/api/v1/spaces/{tenant}/knowledge/answers/{run['id']}"
        auth = {"Authorization": "Bearer " + owner["access_token"]}
        assert live.get(root).status_code == 401
        assert live.get(root, headers=auth).json()["data"]["status"] == "unknown"
        history = root.rsplit("/", 1)[0]
        assert live.get(history).status_code == 401
        assert live.get(history, headers=auth).json()["data"] == [finished]
        assert (
            live.get(root, headers={"Authorization": "Bearer " + other["access_token"]}).status_code
            == 404
        )
        with pytest.raises(DBAPIError), get_engine().begin() as conn:
            context(conn, owner)
            conn.execute(
                text("UPDATE research_runs SET question='changed' WHERE id=:id"), {"id": run["id"]}
            )
        with get_engine().begin() as conn:
            context(conn, other)
            assert conn.execute(text("SELECT count(*) FROM research_runs")).scalar_one() == 0
        # Same-space membership still grants no access to someone else's runs.
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenant_members (tenant_id,user_id,role,active) "
                    "VALUES (:t,:u,'member',true)"
                ),
                {"t": owner["tenant_id"], "u": other["user_id"]},
            )
        other_auth = {"Authorization": "Bearer " + other["access_token"]}
        assert live.get(history, headers=other_auth).json()["data"] == []
        assert live.get(root, headers=other_auth).status_code == 404
        with get_engine().begin() as conn:
            context(conn, owner)
            assert (
                conn.execute(
                    text("UPDATE research_runs SET status='pending' WHERE id=:id"),
                    {"id": run["id"]},
                ).rowcount
                == 0
            )
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE tenant_members SET active=false WHERE tenant_id=:t"),
                {"t": owner["tenant_id"]},
            )
        assert live.get(history, headers=auth).status_code == 404


def test_research_empty_real_api_has_no_usage_and_member_cannot_generate():
    with accounts() as (live, admin, (owner, _other)):
        root = f"/api/v1/spaces/{owner['tenant_id']}/knowledge/answers"
        auth = {"Authorization": "Bearer " + owner["access_token"]}
        body = request("无证据")
        response = live.post(root, headers=auth, json=body.model_dump(mode="json"))
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["status"] == "succeeded" and result["answer"]["insufficient_evidence"]
        assert result["citations"] == []
        assert (
            asyncio.run(service.generate(identity(owner), UUID(owner["tenant_id"]), body)) == result
        )
        with get_engine().begin() as conn:
            context(conn, owner)
            assert conn.execute(text("SELECT count(*) FROM ai_usage")).scalar_one() == 0
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE tenant_members SET role='member' WHERE tenant_id=:t"),
                {"t": owner["tenant_id"]},
            )
        assert (
            live.post(root, headers=auth, json=request().model_dump(mode="json")).status_code == 403
        )
        assert live.get(root, headers=auth).json()["data"] == [result]
