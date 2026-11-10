"""Create one isolated fixture account, run real browser tests, clean only own rows."""

import argparse
import json
import os
import secrets
import subprocess
from uuid import UUID, uuid4

import httpx
from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.integrations.storage import get_s3_client
from app.modules.identity.access import (
    ActorIdentity,
    authenticate_session,
    member_access,
    token_digest,
)
from app.modules.identity.recovery import create_action, locked_user
from app.modules.notes import service as note_service
from app.modules.notes.models import NoteRun
from app.modules.organization import service as organization_service
from app.modules.organization.content import LabelInput, LabelUpdateInput
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import digest, save_batch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def knowledge_fixture(account: dict, title_marker: str = "", *, long_mode: bool = False) -> None:
    """Persist protocol-only revisions with the real application role and RLS.

    This fixture never fetches a platform or calls a provider. The second account
    keeps source-count and audit assertions in the existing browser suite intact.
    """
    tenant_id = UUID(account["tenant_id"])
    identity = ActorIdentity(UUID(account["user_id"]), token_digest(account["access_token"]))
    body = "浏览器协议夹具：仅提供标题与简介，没有视频字幕；不是真实平台采集或模型生成。"
    if long_mode:
        body = "长文协议：记录3个样本。" + "条件与证据必须核对。" * 2800 + "结尾条件为125毫秒。"
    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, account["access_token"])
        access = member_access(tenant_id, actor)
        batch = save_batch(access, f"https://example.com/browser-protocol-{uuid4()}", uuid4())
        source_id = UUID(batch["sources"][0]["id"])
        snapshots = []
        for version in (1, 2):
            job = ProcessingJob(
                tenant_id=tenant_id,
                requested_by=actor.user.id,
                kind="source_collect",
                status="running",
                idempotency_key=str(uuid4()),
                payload={"source_id": str(source_id)},
            )
            session.add(job)
            session.flush()
            snapshot = SourceSnapshot(
                tenant_id=tenant_id,
                source_id=source_id,
                job_id=job.id,
                created_by=actor.user.id,
                version=version,
                fetched_url="https://example.com/",
                platform="web",
                content_kind="metadata",
                title="浏览器协议快照（非平台采集）",
                author="协议夹具",
                body_text=body,
                raw_sha256=digest(body),
                details={"protocol_fixture": True},
                limitations=["PROTOCOL_FIXTURE_ONLY", "NO_TRANSCRIPT_COLLECTED"],
            )
            session.add(snapshot)
            session.flush()
            snapshots.append(snapshot.id)
            job.status, job.progress = "succeeded", 100
    for version, snapshot_id in enumerate(snapshots, 1):
        run_id, _fresh = note_service.begin_run(identity, tenant_id, snapshot_id, uuid4())
        raw = json.dumps(
            {
                "title": f"浏览器协议笔记{title_marker}版本{version}（无模型调用）",
                "overview": {
                    "text": "该记录只用于浏览器协议验证，没有视频字幕。",
                    "evidence_keys": ["E001"],
                },
                "sections": [
                    {
                        "kind": "core_claims",
                        "items": [
                            {"text": "这不是平台采集或模型生成的验收。", "evidence_keys": ["E001"]}
                        ],
                    }
                ],
                "limitations": ["协议夹具，无模型调用，不能计为真实平台或模型验收"],
            },
            ensure_ascii=False,
        )
        stages = None
        if long_mode:
            from app.modules.notes.content import build_evidence
            from app.modules.notes.long_content import batches

            stages = []
            for group in batches(build_evidence(body)):
                stage = json.loads(raw)
                stage["overview"]["evidence_keys"] = [group[0].key]
                stage["sections"][0]["items"][0]["evidence_keys"] = [group[-1].key]
                stages.append(stage)
        note_service.save_document(identity, tenant_id, run_id, raw, body, stages)


def filter_fixture(account: dict) -> str:
    """Seed multi-page protocol data, never a provider/platform acceptance result.

    Pending runs are test setup through the application role and RLS. This does
    not call begin_run, consume paid inference, or change the product daily quota.
    Only the dedicated disposable browser account receives these fixture rows.
    """
    if os.getenv("APP_ENV") != "test" or os.getenv("RUN_STACK_INTEGRATION") != "1":
        raise RuntimeError("Dedicated test stack opt-in required")
    tenant_id = UUID(account["tenant_id"])
    identity = ActorIdentity(UUID(account["user_id"]), token_digest(account["access_token"]))
    suffix = uuid4().hex
    urls = [f"https://example.com/filter-{suffix}-{i}" for i in range(19)]
    urls.extend(["https://www.bilibili.com/video/BV1ZsDNBMESZ/", "https://youtu.be/dQw4w9WgXcQ"])
    urls[0] += "/筛选_100%"
    body = "浏览器筛选协议夹具：没有真实采集，也没有模型调用。"
    runs = []
    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, account["access_token"])
        access = member_access(tenant_id, actor)
        sources = []
        for start in range(0, len(urls), 20):
            batch = save_batch(access, "\n".join(urls[start : start + 20]), uuid4())
            sources.extend(batch["sources"])
        by_original = {source["original_url"]: source for source in sources}
        sources = [by_original[url] for url in urls]
        save_batch(access, urls[0], uuid4())  # A real rejected duplicate candidate.
        for index, source in enumerate(sources):
            source_id = UUID(source["id"])
            job = ProcessingJob(
                tenant_id=tenant_id,
                requested_by=actor.user.id,
                kind="source_collect",
                status="running",
                idempotency_key=str(uuid4()),
                payload={"source_id": str(source_id)},
            )
            session.add(job)
            session.flush()
            snapshot = SourceSnapshot(
                tenant_id=tenant_id,
                source_id=source_id,
                job_id=job.id,
                created_by=actor.user.id,
                version=1,
                fetched_url=source["canonical_url"],
                platform="web" if index < 19 else "bilibili" if index == 19 else "youtube",
                content_kind="article" if index < 19 else "metadata",
                title="筛选协议快照（非平台采集）",
                author="协议夹具",
                body_text=body,
                raw_sha256=digest(body),
                details={"protocol_fixture": True},
                limitations=["PROTOCOL_FIXTURE_ONLY"],
            )
            session.add(snapshot)
            session.flush()
            job.status, job.progress = "succeeded", 100
            for version in (1, 2) if index == 0 else (1,):
                run = NoteRun(
                    tenant_id=tenant_id,
                    snapshot_id=snapshot.id,
                    created_by=actor.user.id,
                    idempotency_key=uuid4(),
                    status="pending",
                )
                session.add(run)
                session.flush()
                runs.append((index, version, run.id))
    special_note_id = ""
    for index, version, run_id in runs:
        special = index == 0
        title = (
            "筛选_100%"
            if special and version == 2
            else "旧版独有词"
            if special
            else f"筛选协议笔记 {index:02d}（无模型调用）"
        )
        raw = json.dumps(
            {
                "title": title,
                "overview": {
                    "text": "筛选正文词" if special and version == 2 else "仅验证列表协议。",
                    "evidence_keys": ["E001"],
                },
                "sections": [
                    {
                        "kind": "core_claims",
                        "items": [{"text": "这只是协议测试。", "evidence_keys": ["E001"]}],
                    }
                ],
                "limitations": ["协议夹具，无模型调用，不能计为真实平台或模型验收"],
            },
            ensure_ascii=False,
        )
        result = note_service.save_document(identity, tenant_id, run_id, raw, body)
        if special:
            special_note_id = result["note_id"]
    return special_note_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-file",
        action="append",
        choices=(
            "account.spec.ts",
            "audit.spec.ts",
            "files.spec.ts",
            "jobs.spec.ts",
            "models.spec.ts",
            "security-events.spec.ts",
            "imports.spec.ts",
            "note-edits.spec.ts",
            "sources.spec.ts",
            "dashboard.spec.ts",
            "note-labels.spec.ts",
            "topic-hierarchy.spec.ts",
            "concept-cards.spec.ts",
            "project-progress.spec.ts",
            "knowledge-explorer.spec.ts",
            "semantic-search.spec.ts",
            "notifications.spec.ts",
            "collections.spec.ts",
            "note-links.spec.ts",
            "note-graph.spec.ts",
            "content-filters.spec.ts",
            "note-annotations.spec.ts",
            "knowledge.spec.ts",
            "product-workspace.spec.ts",
            "long-analysis.spec.ts",
        ),
        help="Run a focused existing browser regression; default runs the full suite",
    )
    arguments = parser.parse_args()
    if os.getenv("APP_ENV") != "test" or os.getenv("RUN_STACK_INTEGRATION") != "1":
        raise RuntimeError("Dedicated test stack opt-in required")
    email, password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    accounts = []
    knowledge_email, knowledge_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    edit_email, edit_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    filter_email, filter_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    label_email, label_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    hierarchy_email, hierarchy_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    hierarchy_labels: dict[str, str] = {}
    annotation_email = f"{uuid4()}@example.invalid"
    annotation_password = secrets.token_urlsafe(32)
    link_email = f"{uuid4()}@example.invalid"
    link_password = secrets.token_urlsafe(32)
    explorer_email, explorer_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    product_email, product_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    product_account: dict = {}
    long_email, long_password = f"{uuid4()}@example.invalid", secrets.token_urlsafe(32)
    with httpx.Client(base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20) as client:
        response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
        response.raise_for_status()
        account = response.json()["data"]
        accounts.append(account)
    try:
        if not arguments.test_file or "long-analysis.spec.ts" in arguments.test_file:
            with httpx.Client(
                base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
            ) as client:
                response = client.post(
                    "/api/v1/auth/register", json={"email": long_email, "password": long_password}
                )
                response.raise_for_status()
                long_account = response.json()["data"]
                accounts.append(long_account)
            knowledge_fixture(long_account, "长文", long_mode=True)
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": knowledge_email, "password": knowledge_password},
            )
            response.raise_for_status()
            knowledge_account = response.json()["data"]
            accounts.append(knowledge_account)
        knowledge_fixture(knowledge_account)
        # Editing has its own protocol note. Test order cannot change the
        # read-only knowledge fixture or turn this into model inference evidence.
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": edit_email, "password": edit_password},
            )
            response.raise_for_status()
            edit_account = response.json()["data"]
            accounts.append(edit_account)
        knowledge_fixture(edit_account)
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": filter_email, "password": filter_password},
            )
            response.raise_for_status()
            filter_account = response.json()["data"]
            accounts.append(filter_account)
        filter_note_id = filter_fixture(filter_account)
        # Classification mutations use their own protocol account, independent
        # of editing/filtering test order; this never calls a model or platform.
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": label_email, "password": label_password},
            )
            response.raise_for_status()
            label_account = response.json()["data"]
            accounts.append(label_account)
            knowledge_fixture(label_account)
            response = client.get(
                f"/api/v1/spaces/{label_account['tenant_id']}/notes",
                headers={"Authorization": "Bearer " + label_account["access_token"]},
            )
            response.raise_for_status()
            label_note_id = response.json()["data"][0]["id"]
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": annotation_email, "password": annotation_password},
            )
            response.raise_for_status()
            annotation_account = response.json()["data"]
            accounts.append(annotation_account)
        knowledge_fixture(annotation_account)
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": link_email, "password": link_password},
            )
            response.raise_for_status()
            link_account = response.json()["data"]
            accounts.append(link_account)
        knowledge_fixture(link_account, "甲")
        knowledge_fixture(link_account, "乙")
        # Explorer exercises its own links/annotations; never mutate another
        # browser case's revision or relationship fixture.
        with httpx.Client(
            base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": explorer_email, "password": explorer_password},
            )
            response.raise_for_status()
            explorer_account = response.json()["data"]
            accounts.append(explorer_account)
        knowledge_fixture(explorer_account, "检索甲")
        knowledge_fixture(explorer_account, "检索乙")
        if not arguments.test_file or "product-workspace.spec.ts" in arguments.test_file:
            with httpx.Client(
                base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
            ) as client:
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": product_email,
                        "password": product_password,
                    },
                )
                response.raise_for_status()
                product_account = response.json()["data"]
                accounts.append(product_account)
                # Reuse the explorer fixture as an ordinary member. No new
                # login buckets or synthetic tenant data needed for visibility.
                response = client.post(
                    f"/api/v1/spaces/{product_account['tenant_id']}/members",
                    headers={"Authorization": "Bearer " + product_account["access_token"]},
                    json={"user_id": explorer_account["user_id"], "role": "member"},
                )
                response.raise_for_status()
        if not arguments.test_file or "topic-hierarchy.spec.ts" in arguments.test_file:
            with httpx.Client(
                base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=20
            ) as client:
                response = client.post(
                    "/api/v1/auth/register",
                    json={"email": hierarchy_email, "password": hierarchy_password},
                )
                response.raise_for_status()
                hierarchy_account = response.json()["data"]
                accounts.append(hierarchy_account)
            hierarchy_identity = ActorIdentity(
                UUID(hierarchy_account["user_id"]), token_digest(hierarchy_account["access_token"])
            )
            hierarchy_labels = {
                name: organization_service.create_label(
                    hierarchy_identity,
                    UUID(hierarchy_account["tenant_id"]),
                    LabelInput(kind=kind, name=name),
                )["id"]
                for name, kind in (
                    ("主题基础", "topic"),
                    ("电路实践", "topic"),
                    ("实验记录", "topic"),
                    ("参考标签", "tag"),
                )
            }
            organization_service.update_label(
                hierarchy_identity,
                UUID(hierarchy_account["tenant_id"]),
                UUID(hierarchy_labels["电路实践"]),
                LabelUpdateInput(parent_id=UUID(hierarchy_labels["主题基础"]), expected_version=1),
            )
            organization_service.update_label(
                hierarchy_identity,
                UUID(hierarchy_account["tenant_id"]),
                UUID(hierarchy_labels["实验记录"]),
                LabelUpdateInput(parent_id=UUID(hierarchy_labels["电路实践"]), expected_version=1),
            )
        # Single-use links for the isolated fixture only. Never print them or
        # persist them in screenshots/traces; verification is still a real API POST.
        engine = create_engine(os.environ["DATABASE_URL"], hide_parameters=True)
        with Session(engine) as session, session.begin():
            user = locked_user(session, UUID(account["user_id"]))
            verify_token = create_action(session, user, "verify_email")
            reset_token = create_action(session, user, "reset_password")
        engine.dispose()
        environment = {
            **os.environ,
            "RUN_ACCOUNT_E2E": "1",
            "E2E_ACCOUNT_EMAIL": email,
            "E2E_LONG_EMAIL": long_email,
            "E2E_LONG_PASSWORD": long_password,
            "E2E_ACCOUNT_PASSWORD": password,
            "E2E_VERIFY_TOKEN": verify_token,
            "E2E_RESET_TOKEN": reset_token,
            "E2E_KNOWLEDGE_EMAIL": knowledge_email,
            "E2E_KNOWLEDGE_PASSWORD": knowledge_password,
            "E2E_NOTE_EDIT_EMAIL": edit_email,
            "E2E_NOTE_EDIT_PASSWORD": edit_password,
            "E2E_FILTER_EMAIL": filter_email,
            "E2E_FILTER_PASSWORD": filter_password,
            "E2E_FILTER_NOTE_ID": filter_note_id,
            "E2E_LABEL_EMAIL": label_email,
            "E2E_LABEL_PASSWORD": label_password,
            "E2E_LABEL_NOTE_ID": label_note_id,
            "E2E_HIERARCHY_EMAIL": hierarchy_email,
            "E2E_HIERARCHY_PASSWORD": hierarchy_password,
            "E2E_HIERARCHY_LABELS": json.dumps(hierarchy_labels),
            "E2E_ANNOTATION_EMAIL": annotation_email,
            "E2E_ANNOTATION_PASSWORD": annotation_password,
            "E2E_NOTE_LINK_EMAIL": link_email,
            "E2E_NOTE_LINK_PASSWORD": link_password,
            "E2E_EXPLORER_EMAIL": explorer_email,
            "E2E_EXPLORER_PASSWORD": explorer_password,
            "E2E_PRODUCT_EMAIL": product_email,
            "E2E_PRODUCT_PASSWORD": product_password,
            "E2E_PRODUCT_TENANT": product_account["tenant_id"]
            if (not arguments.test_file or "product-workspace.spec.ts" in arguments.test_file)
            else "",
        }
        subprocess.run(
            [
                "npm.cmd" if os.name == "nt" else "npm",
                "run",
                "test:e2e",
                "--workspace=@knowledge-os/web",
                "--",
                *(arguments.test_file or []),
            ],
            env=environment,
            check=True,
        )
    finally:
        engine = create_engine(
            os.environ["MIGRATION_DATABASE_URL"], connect_args={"connect_timeout": 3}
        )
        if product_account:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM tenant_members WHERE tenant_id=:t AND user_id=:u"),
                    {"t": product_account["tenant_id"], "u": explorer_account["user_id"]},
                )
        for fixture_account in accounts:
            with engine.begin() as connection:
                cleanup_fixture(connection, fixture_account)
        engine.dispose()


def cleanup_fixture(connection, account: dict) -> None:
    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
    # Match Worker tenant -> job -> dispatch lock ordering during fixture cleanup.
    connection.execute(text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params)
    keys = connection.execute(
        text("SELECT storage_key FROM media_assets WHERE tenant_id=:tenant"), params
    ).scalars()
    for key in keys:
        get_s3_client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
    connection.execute(text("DELETE FROM media_assets WHERE tenant_id=:tenant"), params)
    for table in (
        "notification_deliveries",
        "collection_items",
        "collections",
        "notification_ticks",
        "notification_preferences",
        "semantic_chunks",
        "source_schedule_jobs",
        "source_schedule_ticks",
        "source_schedules",
        "note_learning",
        "research_citations",
        "research_runs",
        "knowledge_concepts",
        "note_links",
        "note_annotations",
        "note_labels",
        "knowledge_labels",
        "evidence_items",
        "note_sections",
        "note_revisions",
        "notes",
        "summary_checkpoints",
        "note_runs",
        "source_snapshots",
        "sources",
        "source_batches",
        "audit_events",
        "ai_usage",
        "ai_providers",
        "user_settings",
    ):
        connection.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
    for table in (
        "job_dispatches",
        "job_steps",
        "job_attempts",
        "processing_jobs",
        "tenant_members",
    ):
        connection.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
    for table in (
        "auth_security_events",
        "auth_action_tokens",
        "auth_sessions",
        "auth_identities",
    ):
        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
    connection.execute(text("DELETE FROM users WHERE id=:user"), params)


if __name__ == "__main__":
    main()
