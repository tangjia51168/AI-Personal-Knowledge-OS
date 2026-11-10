"""Restart acceptance markers; no user data is touched, no volumes are deleted.

Only for scripts/acceptance.ps1 against its dedicated project. The probe table is
created in a separate schema and its row is removed only after successful verification.
"""

import argparse

from app.core.config import get_settings
from app.integrations.storage import get_s3_client
from redis import Redis
from sqlalchemy import create_engine, text

MARKER = "knowledge-os-acceptance-v1"
KEY = "acceptance/persistence-v1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "verify"))
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env != "test" or settings.migration_database_url is None:
        raise SystemExit("Persistence probes require the dedicated test environment")
    engine = create_engine(settings.migration_database_url.get_secret_value())
    storage = get_s3_client()
    with Redis.from_url(settings.redis_url.get_secret_value()) as redis:
        try:
            if args.mode == "write":
                with engine.begin() as connection:
                    connection.execute(text("CREATE SCHEMA IF NOT EXISTS acceptance_checks"))
                    connection.execute(
                        text(
                            "CREATE TABLE IF NOT EXISTS acceptance_checks.persistence "
                            "(id text PRIMARY KEY, value text NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO acceptance_checks.persistence VALUES (:id,:value) "
                            "ON CONFLICT (id) DO UPDATE SET value=EXCLUDED.value"
                        ),
                        {"id": KEY, "value": MARKER},
                    )
                redis.set(KEY, MARKER)
                storage.put_object(Bucket=settings.s3_bucket, Key=KEY, Body=MARKER.encode())
                print("Persistence markers written to isolated PostgreSQL, Redis and MinIO")
            else:
                with engine.connect() as connection:
                    assert (
                        connection.execute(
                            text("SELECT value FROM acceptance_checks.persistence WHERE id=:id"),
                            {"id": KEY},
                        ).scalar()
                        == MARKER
                    )
                assert redis.get(KEY) == MARKER.encode()
                response = storage.get_object(Bucket=settings.s3_bucket, Key=KEY)
                try:
                    assert response["Body"].read() == MARKER.encode()
                finally:
                    response["Body"].close()
                # Remove ONLY this probe's known markers after all checks pass.
                with engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM acceptance_checks.persistence WHERE id=:id"), {"id": KEY}
                    )
                redis.delete(KEY)
                storage.delete_object(Bucket=settings.s3_bucket, Key=KEY)
                print("PASS: PostgreSQL, Redis and MinIO markers survived dependency restart")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
