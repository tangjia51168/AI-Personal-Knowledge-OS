#!/bin/sh
set -eu
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be provided}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE knowledge_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO knowledge_app;
SQL
