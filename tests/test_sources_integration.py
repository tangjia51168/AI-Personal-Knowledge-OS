"""Real source API/DB acceptance; no platform fetch or model request is performed."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.session import get_engine
from app.modules.identity.access import authenticate_session, member_access
from app.modules.sources import service
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def source_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(
        settings.migration_database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as live:
        try:
            for _ in range(2):
                response = live.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "source-intake-acceptance-password-2026",
                    },
                )
                assert response.status_code == 201
                accounts.append(response.json()["data"])
            yield live, admin, accounts
        finally:
            # Only these two newly registered accounts and their own tenant rows.
            with admin.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                for account in accounts:
                    params = {"tenant": account["tenant_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    for table in (
                        "sources",
                        "source_batches",
                        "audit_events",
                        "ai_usage",
                        "ai_providers",
                        "user_settings",
                        "tenant_members",
                    ):
                        connection.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params
                        )
                for account in accounts:
                    params = {"user": account["user_id"], "tenant": account["tenant_id"]}
                    for table in (
                        "auth_security_events",
                        "auth_action_tokens",
                        "auth_sessions",
                        "auth_identities",
                    ):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            admin.dispose()


def auth(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def endpoint(account):
    return f"/api/v1/spaces/{account['tenant_id']}/sources"


@contextmanager
def access_for(account, tenant=None):
    # Real application role, normal opaque-session authentication and membership.
    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, account["access_token"])
        yield member_access(UUID(tenant or account["tenant_id"]), actor)


def save(account, value, key=None, tenant=None):
    with access_for(account, tenant) as access:
        return service.save_batch(access, value, key or uuid4())


def counts(admin, tenant):
    with admin.connect() as connection:
        return tuple(
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant}
            ).scalar_one()
            for table in ("sources", "source_batches", "audit_events")
        )


def set_context(connection, account):
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:user,true)"
        ),
        {"tenant": account["tenant_id"], "user": account["user_id"]},
    )


def test_real_source_filtered_keyset_pages_and_tenant_cursor(source_accounts):
    live, _admin, (owner, other) = source_accounts
    marker = "pagination-" + uuid4().hex
    values = [f"https://example.com/{marker}/{index}" for index in range(7)]
    values.append(values[0] + "#duplicate")
    saved = save(owner, " ".join(values))["sources"]
    assert len({row["created_at"] for row in saved}) == 1
    expected = [row["id"] for row in sorted(saved, key=lambda row: row["id"], reverse=True)]
    params = {"q": "  " + marker.upper() + "  ", "limit": 2}
    received = []
    cursors = []
    while True:
        response = live.get(endpoint(owner), headers=auth(owner), params=params)
        assert response.status_code == 200
        result = response.json()
        received.extend(row["id"] for row in result["data"])
        assert len(result["data"]) <= 2
        if result["next_cursor"] is None:
            break
        assert result["next_cursor"] not in cursors
        cursors.append(result["next_cursor"])
        params["cursor"] = result["next_cursor"]
    assert received == expected and len(cursors) == 3
    rejected = live.get(
        endpoint(owner), headers=auth(owner), params={"q": marker, "status": "rejected"}
    ).json()["data"]
    assert len(rejected) == 1 and rejected[0]["rejection_code"] == "DUPLICATE_CANONICAL_URL"
    for params in (
        {"q": "missing-" + marker, "cursor": expected[0]},
        {"cursor": str(uuid4())},
    ):
        response = live.get(endpoint(owner), headers=auth(owner), params=params)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SOURCE_CURSOR_NOT_FOUND"
    response = live.get(endpoint(other), headers=auth(other), params={"cursor": expected[0]})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SOURCE_CURSOR_NOT_FOUND"
    assert live.get(endpoint(owner), params={"cursor": expected[0]}).status_code == 401
    assert live.get(endpoint(owner), headers=auth(other)).status_code == 404


def test_real_source_platform_boundaries_and_literal_query(source_accounts):
    live, _admin, (owner, _other) = source_accounts
    marker = "platform-" + uuid4().hex
    domains = {
        "bilibili": ["bilibili.com", "a.bilibili.com:8443", "b23.tv"],
        "youtube": ["youtu.be", "www.youtube-nocookie.com"],
        "douyin": ["v.douyin.com"],
        "xiaohongshu": ["xhslink.com"],
        "zhihu": ["www.zhihu.com"],
        "web": [
            "bilibili.com.evil.example",
            "evil-bilibili.com",
            "example.com",
            "[2606:4700:4700::1111]",
        ],
    }
    urls = [f"https://{host}/{marker}/bilibili.com" for hosts in domains.values() for host in hosts]
    urls.append(f"https://example.com/{marker}/literal%_\\tail!/end")
    saved = save(owner, " ".join(urls))["sources"]
    assert all(row["status"] == "accepted" for row in saved)
    for platform, hosts in domains.items():
        response = live.get(
            endpoint(owner), headers=auth(owner), params={"q": marker, "platform": platform}
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) == len(hosts) + (platform == "web")
    literal = live.get(endpoint(owner), headers=auth(owner), params={"q": "%_\\tail!"}).json()[
        "data"
    ]
    assert len(literal) == 1 and literal[0]["canonical_url"] == urls[-1]
    # Rejected canonical=NULL includes duplicate rejections; it is not web.
    save(owner, urls[0] + "#duplicate")
    response = live.get(
        endpoint(owner),
        headers=auth(owner),
        params={"q": marker, "status": "rejected", "platform": "web"},
    )
    assert response.json()["data"] == []


def test_live_source_filter_bounds_are_enforced_after_authorization(source_accounts):
    live, _admin, (owner, _other) = source_accounts
    for params in (
        {"q": "x" * 121},
        {"q": "😀" * 121},
        {"limit": 0},
        {"limit": 51},
        {"limit": "NaN"},
        {"cursor": "invalid"},
        {"platform": "unknown"},
        {"status": "all"},
    ):
        response = live.get(endpoint(owner), headers=auth(owner), params=params)
        assert response.status_code == 422
    response = live.get(endpoint(owner), headers=auth(owner), params={"q": "😀" * 120, "limit": 50})
    assert response.status_code == 200 and response.json()["data"] == []


def test_live_source_roundtrip_replay_conflict_and_cross_tenant(source_accounts):
    live, admin, (owner, other) = source_accounts
    url = f"https://example.com/{uuid4()}"
    key = str(uuid4())
    original = url.replace("example.com", "Example.COM") + "#original"
    value = original + " " + url + "#duplicate https://user:secret@example.com/private"
    before = counts(admin, owner["tenant_id"])
    request = {"text": value, "idempotency_key": key}
    created = live.post(endpoint(owner), headers=auth(owner), json=request)
    assert created.status_code == 201
    result = created.json()["data"]
    assert (result["accepted_count"], result["rejected_count"]) == (1, 2)
    accepted = next(row for row in result["sources"] if row["status"] == "accepted")
    assert accepted["original_url"] == original and accepted["canonical_url"] == url
    assert {row["rejection_code"] for row in result["sources"]} == {
        None,
        "DUPLICATE_CANONICAL_URL",
        "URL_CREDENTIALS_OR_AUTHORITY",
    }
    blocked = next(
        row for row in result["sources"] if row["rejection_code"] == "URL_CREDENTIALS_OR_AUTHORITY"
    )
    assert blocked["original_url"] == "[blocked]" and "secret" not in created.text
    replay = live.post(endpoint(owner), headers=auth(owner), json=request)
    assert replay.status_code == 201 and replay.json()["data"] == result
    conflict = live.post(
        endpoint(owner), headers=auth(owner), json={**request, "text": url + "/changed"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert counts(admin, owner["tenant_id"]) == (before[0] + 3, before[1] + 1, before[2] + 1)
    assert live.get(endpoint(owner)).status_code == 401
    assert live.get(endpoint(owner), headers=auth(other)).status_code == 404
    assert (
        live.post(
            endpoint(owner),
            headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]},
            json={"text": url, "idempotency_key": str(uuid4())},
        ).status_code
        == 404
    )
    own_other = live.post(
        endpoint(other), headers=auth(other), json={"text": url, "idempotency_key": str(uuid4())}
    )
    assert own_other.status_code == 201 and own_other.json()["data"]["accepted_count"] == 1
    listed = live.get(endpoint(owner), headers=auth(owner)).json()["data"]
    assert accepted["id"] in {row["id"] for row in listed}
    assert not (
        {row["id"] for row in own_other.json()["data"]["sources"]} & {row["id"] for row in listed}
    )


def test_real_source_concurrent_deduplication_and_idempotency(source_accounts):
    _live, _admin, (owner, _other) = source_accounts
    url = f"https://example.com/concurrent/{uuid4()}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(owner, url), range(2)))
    assert sorted(result["accepted_count"] for result in results) == [0, 1]
    assert sum(result["rejected_count"] for result in results) == 1
    key = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(owner, url + "/replay", key), range(2)))
    assert results[0] == results[1]


def test_real_source_unicode_identity_uses_bounded_hash_index(source_accounts):
    _live, admin, (owner, _other) = source_accounts
    url = f"https://example.com/{uuid4()}/" + "知识" * 700
    assert len(url) < 2048 and len(url.encode()) > 2704
    result = save(owner, url)
    assert result["accepted_count"] == 1
    assert save(owner, url)["rejected_count"] == 1
    with admin.connect() as connection:
        row = connection.execute(
            text("SELECT canonical_url,canonical_sha256 FROM sources WHERE id=:id"),
            {"id": result["sources"][0]["id"]},
        ).one()
        assert row.canonical_url == url and row.canonical_sha256 == service.digest(url)


def test_real_source_rls_actor_composite_fk_and_append_only(source_accounts):
    _live, admin, (owner, other) = source_accounts
    result = save(owner, f"https://example.com/policy/{uuid4()}")
    other_result = save(other, f"https://example.com/policy/{uuid4()}")
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM sources")).scalar_one() == 0
        set_context(connection, owner)
        assert (
            connection.execute(
                text("SELECT count(*) FROM sources WHERE tenant_id=:tenant"),
                {"tenant": other["tenant_id"]},
            ).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT id FROM sources WHERE id=:id"), {"id": result["sources"][0]["id"]}
        ).scalar_one()
    for table in ("sources", "source_batches"):
        for operation in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
                set_context(connection, owner)
                connection.execute(text(operation))
            assert error.value.orig.sqlstate == "42501"
    # The second user really is a member: rejection below must bind the actor,
    # rather than merely being the membership FK rejecting an unknown person.
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_members (tenant_id,user_id,role,active) "
                "VALUES (:tenant,:user,'member',true) ON CONFLICT DO NOTHING"
            ),
            {"tenant": owner["tenant_id"], "user": other["user_id"]},
        )
    with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
        set_context(connection, owner)
        connection.execute(
            text(
                "INSERT INTO source_batches "
                "(id,tenant_id,created_by,idempotency_key,input_sha256,"
                "accepted_count,rejected_count) "
                "VALUES (:id,:tenant,:user,:key,:digest,1,0)"
            ),
            {
                "id": uuid4(),
                "tenant": owner["tenant_id"],
                "user": other["user_id"],
                "key": uuid4(),
                "digest": "a" * 64,
            },
        )
    assert error.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
        set_context(connection, {**other, "tenant_id": owner["tenant_id"]})
        connection.execute(
            text(
                "INSERT INTO sources (id,tenant_id,batch_id,original_url,status,rejection_code) "
                "VALUES (:id,:tenant,:batch,'[blocked]','rejected','PRIVATE_IP')"
            ),
            {"id": uuid4(), "tenant": owner["tenant_id"], "batch": result["batch_id"]},
        )
    assert error.value.orig.sqlstate == "42501"
    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_members SET active=false WHERE tenant_id=:tenant AND user_id=:user"
            ),
            {"tenant": owner["tenant_id"], "user": other["user_id"]},
        )
    # Administrator bypasses RLS only for this negative FK assertion; runtime
    # checks above use the real non-bypass application role.
    with pytest.raises(DBAPIError) as error, admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sources (id,tenant_id,batch_id,original_url,status,rejection_code) "
                "VALUES (:id,:tenant,:batch,'[blocked]','rejected','PRIVATE_IP')"
            ),
            {"id": uuid4(), "tenant": owner["tenant_id"], "batch": other_result["batch_id"]},
        )
    assert error.value.orig.sqlstate == "23503"


def test_live_source_membership_revocation_and_actor_key_conflict(source_accounts):
    live, admin, (owner, other) = source_accounts
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_members (tenant_id,user_id,role,active) "
                "VALUES (:tenant,:user,'member',true) ON CONFLICT (tenant_id,user_id) "
                "DO UPDATE SET active=true"
            ),
            {"tenant": owner["tenant_id"], "user": other["user_id"]},
        )
    key, url = uuid4(), f"https://example.com/member/{uuid4()}"
    assert save(other, url, key, owner["tenant_id"])["accepted_count"] == 1
    with pytest.raises(HTTPException) as error:
        save(owner, url, key)
    assert error.value.status_code == 409
    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_members SET active=false WHERE tenant_id=:tenant AND user_id=:user"
            ),
            {"tenant": owner["tenant_id"], "user": other["user_id"]},
        )
    assert live.get(endpoint(owner), headers=auth(other)).status_code == 404
    assert (
        live.post(
            endpoint(owner),
            headers=auth(other),
            json={"text": url, "idempotency_key": str(key)},
        ).status_code
        == 404
    )


def test_real_source_quota_checks_are_atomic_and_replay_remains_available(
    source_accounts, monkeypatch
):
    _live, admin, (owner, _other) = source_accounts
    before = counts(admin, owner["tenant_id"])
    monkeypatch.setattr(service, "MAX_DAILY_BATCHES", before[1] + 1)
    key, url = uuid4(), f"https://example.com/quota/{uuid4()}"
    created = save(owner, url, key)
    assert save(owner, url, key) == created
    with pytest.raises(HTTPException) as error:
        save(owner, url + "/daily")
    assert error.value.status_code == 429
    monkeypatch.setattr(service, "MAX_DAILY_BATCHES", 100)
    monkeypatch.setattr(service, "MAX_SPACE_SOURCES", before[0] + 1)
    with pytest.raises(HTTPException) as error:
        save(owner, url + "/total")
    assert error.value.status_code == 429
    assert counts(admin, owner["tenant_id"]) == (before[0] + 1, before[1] + 1, before[2] + 1)


def test_live_source_candidate_limit_and_empty_input_do_not_persist(source_accounts):
    live, admin, (owner, _other) = source_accounts
    urls = [f"https://example.com/bounded/{uuid4()}" for _ in range(21)]
    before = counts(admin, owner["tenant_id"])
    for size, truncated in ((20, False), (21, True)):
        response = live.post(
            endpoint(owner) + "/inspect", headers=auth(owner), json={"text": " ".join(urls[:size])}
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["candidates"]) == 20
        assert response.json()["data"]["truncated"] is truncated
    for value, code in ((" ".join(urls), "SOURCE_CANDIDATE_LIMIT"), ("no links", "NO_SOURCE_URLS")):
        response = live.post(
            endpoint(owner),
            headers=auth(owner),
            json={"text": value, "idempotency_key": str(uuid4())},
        )
        assert response.status_code == 422 and response.json()["error"]["code"] == code
    assert counts(admin, owner["tenant_id"]) == before


def test_real_source_audit_failure_rolls_back_whole_batch(source_accounts, monkeypatch):
    _live, admin, (owner, _other) = source_accounts
    before = counts(admin, owner["tenant_id"])
    record = service.record_event

    def fail_after_audit(*args):
        record(*args)
        args[0].actor.session.flush()
        raise RuntimeError("source-test-transaction-rollback")

    monkeypatch.setattr(service, "record_event", fail_after_audit)
    with pytest.raises(RuntimeError, match="source-test-transaction-rollback"):
        save(owner, f"https://example.com/rollback/{uuid4()}")
    assert counts(admin, owner["tenant_id"]) == before
