#!/usr/bin/env bash
set -euo pipefail

workspace=$(mktemp -d)
container="member-services-smoke-$$"
mock_pid=''
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  if [[ -n "$mock_pid" ]]; then
    kill "$mock_pid" 2>/dev/null || true
    wait "$mock_pid" 2>/dev/null || true
  fi
  rm -f -- "$workspace/health" "$workspace/kb/plan_a/smoke.md" "$workspace/kb/plan_b/smoke.md"
  rmdir -- "$workspace/kb/plan_a" "$workspace/kb/plan_b" "$workspace/kb" "$workspace" 2>/dev/null || true
}
trap cleanup EXIT

for tenant in plan_a plan_b; do
  mkdir -p "$workspace/kb/$tenant"
  cat > "$workspace/kb/$tenant/smoke.md" <<EOF
---
tenant: $tenant
plan_year: 2026
effective_date: 2026-01-01
product_code: TEST
---
| Specialist visit | Synthetic policy |
EOF
done
printf '%s\n' '{"status":"ok","clock":"2026-01-01"}' > "$workspace/health"
chmod a+rx "$workspace" "$workspace/kb" "$workspace/kb/plan_a" "$workspace/kb/plan_b"
chmod a+r "$workspace"/kb/*/smoke.md "$workspace/health"
python -m http.server 18090 --bind 127.0.0.1 --directory "$workspace" >/dev/null 2>&1 &
mock_pid=$!

for attempt in {1..20}; do
  if curl --fail --silent --max-time 1 http://127.0.0.1:18090/health >/dev/null; then
    break
  fi
  sleep 0.25
done
curl --fail --silent --max-time 1 http://127.0.0.1:18090/health >/dev/null

# Host networking is supported on the Linux CI runner. Both listeners bind to loopback.
docker run --detach --name "$container" --network host \
  --mount "type=bind,src=$workspace,dst=/pack,readonly" \
  --env ASSIGNMENT_PACK=/pack --env MOCK_URL=http://127.0.0.1:18090 \
  --env 'REP_CREDENTIALS_JSON={}' --env 'TENANT_TOKENS_JSON={}' --env DIRECTORY_KEY= \
  member-services:ci \
  uvicorn member_services.api:create_app --factory --host 127.0.0.1 --port 18000 --no-access-log

for attempt in {1..20}; do
  if response=$(curl --fail --silent --max-time 1 http://127.0.0.1:18000/health); then
    [[ "$response" == '{"status":"ok"}' ]]
    [[ "$(docker exec "$container" id -u)" != 0 ]]
    docker exec "$container" test -s /state/state.sqlite
    exit 0
  fi
  sleep 0.5
done
docker logs "$container"
exit 1
