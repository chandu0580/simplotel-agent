#!/bin/sh
# Runs once, on first database initialisation (docker-entrypoint-initdb.d).
# Creates the least-privileged application role: no superuser, no BYPASSRLS, DML only.
# Schema changes are made by the owner role through `python -m app.db.migrate`.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$SA_DB_APP_PASSWORD" <<'SQL'
CREATE ROLE simplotel_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'app_password';
GRANT CONNECT ON DATABASE simplotel TO simplotel_app;
GRANT USAGE ON SCHEMA public TO simplotel_app;
ALTER DEFAULT PRIVILEGES FOR ROLE simplotel_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO simplotel_app;
ALTER DEFAULT PRIVILEGES FOR ROLE simplotel_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO simplotel_app;
SQL
