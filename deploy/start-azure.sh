#!/usr/bin/env bash
# Initialize database roles/schema before starting the public, read-only API.
set -euo pipefail
test "$(id -u)" = 0
cd "$(dirname "$0")/.."
set -a
source "${1:-/srv/coffee/.env}"
set +a
for name in POSTGRES_ADMIN_PASSWORD COFFEE_PIPELINE_DB_PASSWORD COFFEE_API_DB_PASSWORD; do
  [[ "${!name:-}" =~ ^[a-f0-9]{64}$ ]] || { echo "Invalid generated credential: $name" >&2; exit 1; }
done
compose=(docker compose --project-name coffee --env-file "${1:-/srv/coffee/.env}" -f deploy/compose.azure.yaml)
# Sequential builds keep peak memory lower on the 1 GiB VM.
"${compose[@]}" build api
"${compose[@]}" build web
"${compose[@]}" up -d --wait postgres
"${compose[@]}" exec -T postgres psql -U postgres -d coffee_price -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'coffee_pipeline') THEN
    CREATE ROLE coffee_pipeline LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'coffee_api') THEN
    CREATE ROLE coffee_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END \$\$;
ALTER ROLE coffee_pipeline PASSWORD '$COFFEE_PIPELINE_DB_PASSWORD';
ALTER ROLE coffee_api PASSWORD '$COFFEE_API_DB_PASSWORD';
ALTER DATABASE coffee_price OWNER TO coffee_pipeline;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO coffee_pipeline;
GRANT USAGE ON SCHEMA public TO coffee_api;
ALTER DEFAULT PRIVILEGES FOR ROLE coffee_pipeline IN SCHEMA public GRANT SELECT ON TABLES TO coffee_api;
SQL
export PGPASSWORD="$COFFEE_PIPELINE_DB_PASSWORD"
"${compose[@]}" run --rm --no-deps -e PGUSER=coffee_pipeline -e PGPASSWORD --entrypoint python api -m coffee_service.db
"${compose[@]}" exec -T postgres psql -U postgres -d coffee_price -v ON_ERROR_STOP=1 <<'SQL'
GRANT SELECT ON ALL TABLES IN SCHEMA public TO coffee_api;
SQL
unset PGPASSWORD
"${compose[@]}" up -d --no-build --wait --wait-timeout 120 api web
"${compose[@]}" ps
