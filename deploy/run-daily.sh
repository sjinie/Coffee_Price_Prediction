#!/usr/bin/env bash
# GitHub runner에서 실행한다(.github/workflows/daily.yml).
# VM에 보관한 소스 Parquet를 받아 파이프라인을 돌리고, SSH 터널로 VM의 PostgreSQL에 적재한 뒤 소스를 돌려놓는다.
# 종료 코드 3(경고)은 예측이 저장된 상태라 Actions 경고로 남기고 성공으로 처리한다.
set -euo pipefail
umask 077

for name in RUNNER_TEMP COFFEE_HOST COFFEE_SSH_USER COFFEE_STATE_DIR COFFEE_SSH_KEY COFFEE_KNOWN_HOSTS \
            PGDATABASE PGUSER PGPASSWORD FRED_API_KEY AI_GATEWAY_API_KEY; do
  [[ -n "${!name:-}" ]] || { printf 'Missing %s\n' "$name" >&2; exit 1; }
done
[[ "$COFFEE_HOST" =~ ^[A-Za-z0-9.-]+$ && "$COFFEE_SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ \
   && "$COFFEE_STATE_DIR" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo 'Invalid SSH destination or state path' >&2; exit 1; }

work=$(mktemp -d "$RUNNER_TEMP/coffee.XXXXXX")
tunnel_pid=
pulled=0
cleanup() {
  local status=$?
  trap - EXIT
  # 수집 단계는 소스 파일을 하나씩 원자적으로 바꾼다. 파이프라인이 실패해도 받은 소스는 돌려놓는다.
  if (( pulled )); then
    rsync -a --delay-updates data/sources/ "coffee-vm:$COFFEE_STATE_DIR/sources/" || { echo 'Source sync failed' >&2; status=1; }
  fi
  if [[ -n "$tunnel_pid" ]]; then
    kill "$tunnel_pid" 2>/dev/null || true
    wait "$tunnel_pid" 2>/dev/null || true
  fi
  rm -rf -- "$work"   # SSH 키를 지운다
  exit "$status"
}
trap cleanup EXIT

printf '%s\n' "$COFFEE_SSH_KEY" > "$work/key"
printf '%s\n' "$COFFEE_KNOWN_HOSTS" > "$work/known_hosts"
cat > "$work/ssh_config" <<EOF
Host coffee-vm
  HostName $COFFEE_HOST
  User $COFFEE_SSH_USER
  IdentityFile $work/key
  UserKnownHostsFile $work/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
  IdentitiesOnly yes
  ConnectTimeout 15
  # 2026-09-28에 포트 22 연결이 한 번 끊겨 배치 전체가 실패했다. 연결마다 다시 시도한다.
  ConnectionAttempts 3
  ServerAliveInterval 15
  ServerAliveCountMax 3
EOF
export RSYNC_RSH="ssh -F $work/ssh_config"

mkdir -p data/sources
rsync -a "coffee-vm:$COFFEE_STATE_DIR/sources/" data/sources/
pulled=1

ssh -F "$work/ssh_config" -N -o ExitOnForwardFailure=yes -L 127.0.0.1:15432:127.0.0.1:15432 coffee-vm &
tunnel_pid=$!
sleep 2
kill -0 "$tunnel_pid" 2>/dev/null || { echo 'SSH tunnel failed' >&2; exit 1; }

export PGHOST=127.0.0.1 PGPORT=15432 DATABASE_URL=postgresql://   # 비밀번호는 PGPASSWORD로만 넘긴다
status=0
python -m coffee.pipeline daily || status=$?
if (( status == 3 )); then
  echo '::warning title=Daily pipeline warning::예측은 저장했지만 일부 소스·피처·뉴스에 경고가 있습니다. 위 로그를 확인하세요.'
  status=0
fi
exit "$status"
