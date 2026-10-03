#!/usr/bin/env bash
# VM에서 root로 실행한다. 새 database·권한·스키마를 준비하고 API·대시보드를 새 이미지로 띄운다.
#   sudo COFFEE_SOURCE_SHA=$(git rev-parse --short HEAD) deploy/setup-db.sh /srv/coffee/.env
# 여러 번 실행해도 된다. 이전 버전의 database(coffee_price)는 건드리지 않는다.
set -euo pipefail
test "$(id -u)" = 0
cd "$(dirname "$0")/.."
env_file=${1:-/srv/coffee/.env}
set -a
source "$env_file"
set +a
for name in POSTGRES_ADMIN_PASSWORD COFFEE_PIPELINE_DB_PASSWORD COFFEE_API_DB_PASSWORD; do
  [[ "${!name:-}" =~ ^[a-f0-9]{64}$ ]] || { echo "Invalid generated credential: $name" >&2; exit 1; }
done
[[ "${COFFEE_DB_NAME:-}" =~ ^[a-z][a-z0-9_]*$ ]] || { echo 'Invalid COFFEE_DB_NAME' >&2; exit 1; }
: "${COFFEE_SOURCE_SHA:?required}"

compose=(docker compose --project-name coffee --env-file "$env_file" -f deploy/compose.azure.yaml)
admin_psql=("${compose[@]}" exec -T postgres psql -U postgres -v ON_ERROR_STOP=1)

# 1 GiB VM에서 메모리를 아끼려고 이미지를 하나씩 만든다.
"${compose[@]}" build api
"${compose[@]}" build web
"${compose[@]}" up -d --wait postgres

# 비밀번호는 위에서 64자리 16진수인지 확인했다. SQL은 표준 입력으로 넘겨 프로세스 목록에 남지 않는다.
"${admin_psql[@]}" -d postgres <<SQL
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
SELECT 'CREATE DATABASE $COFFEE_DB_NAME OWNER coffee_pipeline'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$COFFEE_DB_NAME')\gexec
REVOKE ALL ON DATABASE $COFFEE_DB_NAME FROM PUBLIC;
GRANT CONNECT ON DATABASE $COFFEE_DB_NAME TO coffee_pipeline, coffee_api;
SQL

"${admin_psql[@]}" -d "$COFFEE_DB_NAME" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO coffee_api;
ALTER DEFAULT PRIVILEGES FOR ROLE coffee_pipeline IN SCHEMA public GRANT SELECT ON TABLES TO coffee_api;
SQL
# 스키마는 소유자 계정으로 적용한다(컨테이너 안 로컬 소켓 접속).
"${compose[@]}" exec -T postgres psql -U coffee_pipeline -d "$COFFEE_DB_NAME" -v ON_ERROR_STOP=1 < coffee/schema.sql
"${admin_psql[@]}" -d "$COFFEE_DB_NAME" -c 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO coffee_api;'

# GitHub runner가 rsync로 주고받는 소스 Parquet 보관 폴더
install -d -m 0700 -o coffee-actions -g coffee-actions /srv/coffee/v2 /srv/coffee/v2/sources

"${compose[@]}" up -d --no-build --wait --wait-timeout 120 api web caddy
"${compose[@]}" ps
