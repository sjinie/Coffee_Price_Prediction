#!/usr/bin/env bash
set -euo pipefail
umask 077

for name in RUNNER_TEMP COFFEE_HOST COFFEE_SSH_USER COFFEE_STATE_DIR COFFEE_SSH_KEY COFFEE_KNOWN_HOSTS PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD FRED_API_KEY AI_GATEWAY_API_KEY; do
  [[ -n "${!name:-}" ]] || { printf 'Missing %s\n' "$name" >&2; exit 1; }
done
[[ "$COFFEE_HOST" =~ ^[A-Za-z0-9.-]+$ && "$COFFEE_SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ && "$COFFEE_STATE_DIR" =~ ^/[A-Za-z0-9_./-]+$ ]] || {
  echo 'Invalid SSH destination or state path' >&2
  exit 1
}
[[ "$PGHOST" == 127.0.0.1 && "$PGPORT" == 15432 ]] || { echo 'Invalid tunnel database endpoint' >&2; exit 1; }

work=$(mktemp -d "$RUNNER_TEMP/coffee-pipeline.XXXXXX")
tunnel_pid=
pipeline_pid=
sync_ready=0
cleanup() {
  local status=$?
  trap - EXIT
  trap '' INT TERM
  if (( sync_ready )); then
    local sync_failed=0
    rsync -a --delay-updates "$work/sources/" "coffee-vm:$COFFEE_STATE_DIR/sources/" || sync_failed=1
    rsync -a --delay-updates "$work/jev/" "coffee-vm:$COFFEE_STATE_DIR/jev/" || sync_failed=1
    if (( sync_failed )); then
      echo 'State synchronization failed' >&2
      status=1
    fi
  fi
  if [[ -n "$tunnel_pid" ]]; then
    kill "$tunnel_pid" 2>/dev/null || true
    wait "$tunnel_pid" 2>/dev/null || true
  fi
  rm -rf -- "$work"
  exit "$status"
}
stop_pipeline() {
  local status=$1
  local child=$pipeline_pid
  trap '' INT TERM
  if [[ -z "$child" && "$sync_ready" == 1 ]]; then
    child=$!
  fi
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'stop_pipeline 130' INT
trap 'stop_pipeline 143' TERM

printf '%s\n' "$COFFEE_SSH_KEY" > "$work/key"
printf '%s\n' "$COFFEE_KNOWN_HOSTS" > "$work/known_hosts"
chmod 600 "$work/key" "$work/known_hosts"
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
  ServerAliveInterval 15
  ServerAliveCountMax 3
EOF
export RSYNC_RSH="ssh -F $work/ssh_config"

mkdir -p "$work/sources" "$work/jev" "$work/models"
rsync -a "coffee-vm:$COFFEE_STATE_DIR/sources/" "$work/sources/"
rsync -a "coffee-vm:$COFFEE_STATE_DIR/jev/" "$work/jev/"
rsync -a "coffee-vm:$COFFEE_STATE_DIR/models/production_dlinear_60.pt" "$work/models/production_dlinear_60.pt"

ssh -F "$work/ssh_config" -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:15432:127.0.0.1:15432 coffee-vm &
tunnel_pid=$!
sleep 1
kill -0 "$tunnel_pid" 2>/dev/null || { echo 'SSH tunnel failed' >&2; exit 1; }

pipeline_status=0
sync_ready=1
python -m coffee_service.pipeline refresh --once \
  --source-dir "$work/sources" \
  --jev-cache "$work/jev/responses.json" \
  --artifact "$work/models/production_dlinear_60.pt" &
pipeline_pid=$!
wait "$pipeline_pid" || pipeline_status=$?
pipeline_pid=
exit "$pipeline_status"
