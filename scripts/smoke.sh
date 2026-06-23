#!/usr/bin/env bash
# ABOUTME: End-to-end smoke test — brings the stack up and asserts the whole loop works.
# ABOUTME: Pre-demo confidence check: detection -> label -> retrain -> deploy + enforcement + trace.

set -uo pipefail
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

# wait_for "desc" <timeout_s> <cmd...> — polls until cmd exits 0 (quietly) or times out.
wait_for() {
  local desc="$1" timeout="$2"; shift 2
  local i=0
  until "$@" >/dev/null 2>&1; do
    i=$((i+1))
    if [ "$i" -ge "$timeout" ]; then bad "$desc (timed out after ${timeout}s)"; return 1; fi
    sleep 1
  done
  ok "$desc"
}

psql_q() { docker compose exec -T timescale psql -U postgres -tA -c "$1" 2>/dev/null; }
prom_v() { curl -s "http://localhost:9090/api/v1/query" --data-urlencode "query=$1" \
             | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print(r[0]['value'][1] if r else '')" 2>/dev/null; }

echo "== bringing up the stack (building current code) =="
# --build so the smoke validates the CURRENT code, never a stale image.
docker compose --profile fake up -d --build >/dev/null 2>&1

echo "== readiness =="
wait_for "bridge healthy"      40 bash -c 'curl -sf localhost:8000/healthz'
wait_for "prometheus ready"    40 bash -c 'curl -sf localhost:9090/-/ready'
wait_for "tempo ready"         40 bash -c 'curl -s localhost:3200/ready | grep -q ready'
wait_for "grafana healthy"     40 bash -c 'curl -s localhost:3000/api/health | grep -q ok'

echo "== detection (perturb -> score crosses threshold) =="
curl -s -X POST localhost:8000/control -H 'Content-Type: application/json' -d '{"cmd":"perturb"}' >/dev/null
wait_for "cnc-7 score > 0.60 after perturb" 30 bash -c \
  "docker compose exec -T timescale psql -U postgres -tA -c \"SELECT 1 FROM scores WHERE container_id='cnc-7' AND ts>now()-interval '10s' AND anomaly_score>0.6 LIMIT 1\" | grep -q 1"
[ -n "$(psql_q "SELECT 1 FROM alerts WHERE container_id='cnc-7' LIMIT 1")" ] \
  && ok "alert present for cnc-7" || bad "no alert for cnc-7"

echo "== label loop (Contract D + retrain) =="
curl -s -X POST localhost:8000/label -H 'Content-Type: application/json' \
  -d '{"line":"line1","container_id":"cnc-7","label":"bearing wear"}' >/dev/null
wait_for "label persisted to retrain store" 15 bash -c \
  "docker compose exec -T timescale psql -U postgres -tA -c \"SELECT 1 FROM labels WHERE label<>'dismiss' LIMIT 1\" | grep -q 1"
wait_for "retrain_queued event emitted" 15 bash -c \
  "docker compose exec -T timescale psql -U postgres -tA -c \"SELECT 1 FROM model_events WHERE event_type='retrain_queued' LIMIT 1\" | grep -q 1"

echo "== model deploy (Contract C) =="
curl -s -X POST localhost:8000/deploy -H 'Content-Type: application/json' -d '{"line":"line1"}' >/dev/null
wait_for "deployed event recorded" 15 bash -c \
  "docker compose exec -T timescale psql -U postgres -tA -c \"SELECT 1 FROM model_events WHERE event_type='deployed' LIMIT 1\" | grep -q 1"

echo "== data-minimization enforcement (Vector) =="
wait_for "raw/features blocked at boundary" 30 bash -c \
  "curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=sum(rate(boundary_bytes{disposition=\"blocked\"}[1m]))>0' | grep -q value"
wait_for "inference egressed at boundary" 30 bash -c \
  "curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=sum(rate(boundary_bytes{disposition=\"egressed\"}[1m]))>0' | grep -q value"

echo "== observability (trace survives the Vector hop) =="
# A single trace spanning fakegen + bridge proves body-carried trace context works.
wait_for "trace spans fakegen + bridge" 45 bash -c \
  "curl -s 'http://localhost:3200/api/search?q=%7B%7D&limit=10' | python3 -c \"import sys,json; t=json.load(sys.stdin).get('traces',[]); sys.exit(0 if any({'fakegen','bridge'} <= set(x.get('serviceStats',{}).keys()) for x in t) else 1)\""
wait_for "spanmetrics latency histogram present" 45 bash -c \
  "curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=count(spanmetrics_duration_milliseconds_bucket)' | grep -q value"

# return the line to calm so the dashboard looks healthy after the check
curl -s -X POST localhost:8000/control -H 'Content-Type: application/json' -d '{"cmd":"heal"}' >/dev/null

echo
echo "== smoke result: ${PASS} passed, ${FAIL} failed =="
[ "$FAIL" -eq 0 ] && { echo "READY — the loop works end-to-end."; exit 0; } || { echo "NOT READY — see failures above."; exit 1; }
