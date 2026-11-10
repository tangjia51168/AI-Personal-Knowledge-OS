import os
import time
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.integrations.storage import get_s3_client, tenant_object_key
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated test stack required"
    ),
]


def test_live_tenant_file_upload_download_and_delete():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(settings.migration_database_url.get_secret_value())
    accounts = []
    keys = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=30, trust_env=False) as client:
        try:
            for _ in range(2):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "acceptance-only-password-2026",
                    },
                )
                assert response.status_code == 201, response.text
                accounts.append(response.json()["data"])
            a, b = accounts
            auth = {"Authorization": f"Bearer {a['access_token']}"}
            other = {"Authorization": f"Bearer {b['access_token']}"}
            path = f"/api/v1/spaces/{a['tenant_id']}/assets"
            headers = {**auth, "Content-Type": "text/plain"}
            assert client.post(path, content=b"private").status_code == 401
            assert client.post(path, content=b"private", headers=other).status_code == 404
            assert (
                client.post(
                    path, content=b"bad", headers={**auth, "Content-Type": "application/pdf"}
                ).status_code
                == 415
            )
            assert (
                client.post(path, content=b"x" * (10485760 + 1), headers=headers).status_code == 413
            )
            response = client.post(
                path,
                params={"filename": "../private.txt"},
                content="私有文件内容".encode(),
                headers=headers,
            )
            assert response.status_code == 201, response.text
            asset = response.json()["data"]
            assert asset["status"] == "ready" and "storage_key" not in asset
            asset_id = asset["id"]
            keys.append(tenant_object_key(UUID(a["tenant_id"]), UUID(asset_id)))
            assert client.get(path, headers=auth).json()["data"][0]["id"] == asset_id
            ticket = client.post(f"{path}/{asset_id}/download-ticket", headers=auth).json()["data"]
            download = f"{path}/{asset_id}/download"
            response = client.get(download, params=ticket, headers=auth)
            assert response.status_code == 200 and response.content == "私有文件内容".encode()
            assert response.headers["content-disposition"].startswith("attachment;")
            assert client.get(download, params=ticket).status_code == 401
            assert client.get(download, params=ticket, headers=other).status_code == 404
            assert (
                client.get(
                    download, params={**ticket, "expires": int(time.time()) - 1}, headers=auth
                ).status_code
                == 403
            )
            assert (
                client.get(
                    download, params={**ticket, "signature": "0" * 64}, headers=auth
                ).status_code
                == 403
            )
            assert client.delete(f"{path}/{asset_id}", headers=other).status_code == 404
            assert client.delete(f"{path}/{asset_id}", headers=auth).status_code == 200
            assert client.get(download, params=ticket, headers=auth).status_code == 404
            # Only an explicitly shared active member can obtain their own ticket.
            member_path = f"/api/v1/spaces/{a['tenant_id']}/members"
            assert (
                client.post(member_path, headers=auth, json={"user_id": b["user_id"]}).status_code
                == 201
            )
            response = client.post(path, content=b"shared", headers=headers)
            assert response.status_code == 201, response.text
            shared = response.json()["data"]["id"]
            keys.append(tenant_object_key(UUID(a["tenant_id"]), UUID(shared)))
            shared_ticket = client.post(f"{path}/{shared}/download-ticket", headers=other).json()[
                "data"
            ]
            assert (
                client.get(
                    f"{path}/{shared}/download", params=shared_ticket, headers=auth
                ).status_code
                == 403
            )
            assert client.delete(f"{path}/{shared}", headers=other).status_code == 403
            assert client.delete(f"{member_path}/{b['user_id']}", headers=auth).status_code == 200
            assert (
                client.get(
                    f"{path}/{shared}/download", params=shared_ticket, headers=other
                ).status_code
                == 404
            )
            assert client.delete(f"{path}/{shared}", headers=auth).status_code == 200
            # Pending objects are charged/reserved, not falsely presented as ready.
            pending = uuid4()
            pending_key = tenant_object_key(UUID(a["tenant_id"]), pending)
            keys.append(pending_key)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO media_assets "
                        "(id,tenant_id,uploaded_by,storage_key,mime_type,size,"
                        "status,filename,created_at) "
                        "VALUES (:id,:tenant,:user,:key,'text/plain',3,'pending',"
                        "'interrupted.txt',now()-interval '1 hour')"
                    ),
                    {
                        "id": pending,
                        "tenant": a["tenant_id"],
                        "user": a["user_id"],
                        "key": pending_key,
                    },
                )
            get_s3_client().put_object(Bucket=settings.s3_bucket, Key=pending_key, Body=b"old")
            assert client.post(f"{path}/{pending}/download-ticket", headers=auth).status_code == 409
            assert client.delete(f"{path}/{pending}", headers=auth).status_code == 200
            with engine.begin() as connection:
                events = (
                    connection.execute(
                        text(
                            "SELECT action FROM audit_events "
                            "WHERE tenant_id=:tenant AND resource_id=:asset"
                        ),
                        {"tenant": a["tenant_id"], "asset": asset_id},
                    )
                    .scalars()
                    .all()
                )
                assert sorted(events) == ["file.deleted", "file.uploaded"]
        finally:
            for key in keys:
                get_s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)
            with engine.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    connection.execute(
                        text("DELETE FROM audit_events WHERE tenant_id=:tenant"), params
                    )
                    connection.execute(
                        text("DELETE FROM media_assets WHERE tenant_id=:tenant"), params
                    )
                    connection.execute(
                        text("DELETE FROM tenant_members WHERE tenant_id=:tenant"), params
                    )
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    for table in (
                        "auth_security_events",
                        "auth_sessions",
                        "auth_identities",
                        "tenant_members",
                    ):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            engine.dispose()
