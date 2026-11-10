"""Create local development secrets without overwriting an existing environment file.

Run from the repository root. Values are deliberately never printed.
"""

import argparse
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acceptance", action="store_true", help="Create isolated acceptance config"
    )
    args = parser.parse_args()
    target = Path(".env.acceptance" if args.acceptance else ".env")
    if target.exists():
        raise SystemExit(f"{target} already exists; left unchanged")
    admin_password = secrets.token_hex(24)
    app_password = secrets.token_hex(24)
    db_port = 15432 if args.acceptance else 5432
    redis_port = 16379 if args.acceptance else 6379
    storage_port = 19000 if args.acceptance else 9000
    api_port = 18000 if args.acceptance else 8000
    content = (
        "# Generated local-only secrets. Never commit or print this file.\n"
        "POSTGRES_USER=knowledge_admin\nPOSTGRES_DB=knowledge_os\n"
        f"POSTGRES_PASSWORD={admin_password}\nAPP_DB_PASSWORD={app_password}\n"
        f"DATABASE_URL=postgresql+psycopg://knowledge_app:{app_password}"
        f"@localhost:{db_port}/knowledge_os\n"
        f"MIGRATION_DATABASE_URL=postgresql+psycopg://knowledge_admin:{admin_password}"
        f"@localhost:{db_port}/knowledge_os\n"
        f"REDIS_URL=redis://localhost:{redis_port}/0\n"
        f"S3_ENDPOINT_URL=http://localhost:{storage_port}\n"
        f"S3_ACCESS_KEY=knowledge_{secrets.token_hex(8)}\n"
        f"S3_SECRET_KEY={secrets.token_hex(24)}\n"
        "S3_BUCKET=knowledge-os\nS3_REGION=us-east-1\n"
        f"SYSTEM_API_TOKEN={secrets.token_hex(32)}\n"
        f"DOWNLOAD_SIGNING_KEY={secrets.token_hex(32)}\n"
        f"API_BASE_URL=http://localhost:{api_port}\nAPP_ENV=development\n"
        'CORS_ORIGINS=["http://localhost:3000"]\n'
    )
    # Exclusive create prevents accidental rotation of credentials used by persistent volumes.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    print(f"Created {target}; secrets not displayed. Local development only.")


if __name__ == "__main__":
    main()
