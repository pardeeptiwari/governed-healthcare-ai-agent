#!/usr/bin/env bash
set -euo pipefail

workspace=$(mktemp -d)
container="member-services-smoke-$$"
fixture="member-services-fixture-$$"
network="member-services-smoke-$$"
image=member-services:ci

cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then
    echo "Docker smoke failed. Application container logs:"
    docker logs "$container" 2>&1 || true
    echo "Synthetic fixture container logs:"
    docker logs "$fixture" 2>&1 || true
  fi
  docker stop --time 5 "$container" "$fixture" >/dev/null 2>&1 || true
  docker rm -f "$container" "$fixture" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf -- "$workspace"
  exit "$status"
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
| Specialist visit | \$25 copay | Not covered |
EOF
done

member_fixture="$workspace/upstream/tenants/plan_a/members/M1234"
mkdir -p "$member_fixture"
printf '%s\n' '{"status":"ok","clock":"2026-11-15"}' > "$workspace/upstream/health"
printf '%s\n' '{"member_id":"M1234","coverage_status":"ACTIVE","coverage_termination":null,"product_code":"TEST","plan_name":"Synthetic Smoke Plan","accumulators":{}}' > "$member_fixture/benefits"
chmod a+rX "$workspace"
chmod -R a+rX "$workspace/kb" "$workspace/upstream"

docker network create "$network" >/dev/null
docker run --detach --name "$fixture" --network "$network" --network-alias fixture \
  --mount "type=bind,src=$workspace/upstream,dst=/fixture,readonly" \
  "$image" python -m http.server 8080 --directory /fixture

for attempt in {1..20}; do
  if docker exec "$fixture" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=1).close()' >/dev/null 2>&1; then
    break
  fi
  if (( attempt == 20 )); then
    echo "Synthetic fixture did not become ready."
    exit 1
  fi
  sleep 0.25
done

# Run the image's default command. Only the application is published on runner loopback.
docker run --detach --name "$container" --network "$network" --publish 127.0.0.1::8000 \
  --mount "type=bind,src=$workspace/kb,dst=/pack/kb,readonly" \
  --env ASSIGNMENT_PACK=/pack --env MOCK_URL=http://fixture:8080 \
  --env 'REP_CREDENTIALS_JSON={"smoke-rep":{"tenant":"plan_a","rep":"smoke","member_ids":["M1234"]}}' \
  --env 'TENANT_TOKENS_JSON={"plan_a":"synthetic-tenant-token"}' --env DIRECTORY_KEY= \
  "$image"
base="http://$(docker port "$container" 8000/tcp)"

for attempt in {1..40}; do
  if response=$(curl --fail --silent --max-time 1 "$base/health"); then
    break
  fi
  if (( attempt == 40 )); then
    echo "Application did not become ready."
    exit 1
  fi
  sleep 0.5
done
[[ "$response" == '{"status":"ok"}' ]]
[[ "$(docker exec "$container" id -u)" != 0 ]]
docker exec "$container" test -s /state/state.sqlite

post() {
  curl --silent --show-error --max-time 5 -H 'Authorization: Bearer smoke-rep' \
    -H 'Content-Type: application/json' "$@"
}
denied=$(post --output /dev/null --write-out '%{http_code}' -d '{"member_id":"M9999"}' "$base/v1/conversations")
[[ "$denied" == 404 ]]
started=$(post --fail -d '{"member_id":"M1234"}' "$base/v1/conversations")
conversation=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["conversation_id"])' <<< "$started")
turn="$base/v1/conversations/$conversation/turns"
benefit=$(post --fail -d '{"turn_id":"benefit","message":"What is the specialist copay?"}' "$turn")
python3 -c 'import json, sys; r=json.load(sys.stdin); assert r["outcome"] == "answer" and "$25 copay" in r["answer"] and r["evidence"] == ["smoke.md"]' <<< "$benefit"
declined=$(post --fail -d '{"turn_id":"privacy","message":"Read the full SSN"}' "$turn")
python3 -c 'import json, sys; assert json.load(sys.stdin)["outcome"] == "decline"' <<< "$declined"
echo "Health, non-root runtime, writable state, authorization, benefits and PHI refusal checks passed."
