#!/usr/bin/env bash
# E2E smoke test for FlowSint CLI binary against a local stub API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STUB_PORT="$(
python3 - <<'PY'
import socket

s = socket.socket()
s.bind(("", 0))
print(s.getsockname()[1])
s.close()
PY
)"
export FLOWSINT_API_URL="http://127.0.0.1:${STUB_PORT}"
export FLOWSINT_STUB_PORT="$STUB_PORT"
STUB_PID=""
FAILED=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAILED=1; }

cleanup() {
    rm -f "$PROJECT_DIR/flowsint-e2e"
    [ -n "${NODE_IDS_CSV_PATH:-}" ] && rm -f "$NODE_IDS_CSV_PATH"
    [ -n "${LIST_OUTPUT_FILE:-}" ] && rm -f "$LIST_OUTPUT_FILE" || true
    if [ -n "$STUB_PID" ] && kill -0 "$STUB_PID" 2>/dev/null; then
        kill "$STUB_PID" 2>/dev/null || true
        for _ in $(seq 1 80); do
            if ! kill -0 "$STUB_PID" 2>/dev/null; then
                break
            fi
            sleep 0.05
        done
        if kill -0 "$STUB_PID" 2>/dev/null; then
            kill -s SIGKILL "$STUB_PID" 2>/dev/null || true
        fi
        wait "$STUB_PID" 2>/dev/null || true
    fi
    return 0
}
trap cleanup EXIT INT TERM

echo "=== FlowSint CLI E2E Smoke Test ==="
echo ""

CLI="$PROJECT_DIR/flowsint-e2e"
SAMPLE_ENRICHERS="domain_to_whois,ip_to_asn,website_to_scrapling,social-confidence-aggregator"
TEMPLATE_PATH="$SCRIPT_DIR/fixtures/enricher-template-cookiecutter"
TEMPLATE_CONTEXT='{"template_name":"rendered-template","template_category":"test","template_request_url":"https://dns.example.com/{value}"}'
NODE_IDS_CSV_PATH="$(mktemp)"
printf "node_id\ncsv-node-1\ncsv-node-2\n" > "$NODE_IDS_CSV_PATH"
echo "Regenerating CLI pointing at ${FLOWSINT_API_URL}..."
cd "$PROJECT_DIR"
echo "Using stub port ${STUB_PORT}"
SERVER_JSON='{"name":"flowsint-test","command":"uv","args":["run","--directory","flowsint-mcp-server","flowsint-mcp"],"env":{"FLOWSINT_API_URL":"'"$FLOWSINT_API_URL"'"}}'
WINDMILL_TOKEN=dummy mcporter generate-cli --server "$SERVER_JSON" --compile "$CLI" --runtime bun 2>&1
echo ""

echo "Starting stub API server on port ${STUB_PORT}..."
FLOWSINT_STUB_PORT="$STUB_PORT" python3 "$SCRIPT_DIR/stub_api.py" &
STUB_PID=$!

for i in $(seq 1 50); do
    if curl -s "$FLOWSINT_API_URL/health" >/dev/null 2>&1; then
        echo "Stub API is ready."
        break
    fi
    if ! kill -0 "$STUB_PID" 2>/dev/null; then
        echo "ERROR: Stub API died during startup."
        exit 1
    fi
    sleep 0.2
done

echo "--- health ---"
OUTPUT=$("$CLI" health 2>&1) || { echo "$OUTPUT"; fail "health exit code"; }
if echo "$OUTPUT" | grep -q "API reachable" && echo "$OUTPUT" | grep -q "200"; then
    pass "health confirms API reachable (HTTP 200)"
else
    echo "  output: $OUTPUT"
    fail "health: expected API reachable with HTTP 200"
fi
echo ""

echo "--- list-enrichers ---"
LIST_OUTPUT_FILE="$(mktemp)"
"$CLI" list-enrichers 2>&1 > "$LIST_OUTPUT_FILE" || { cat "$LIST_OUTPUT_FILE"; fail "list-enrichers exit code"; }
ENRICHER_COUNT="$(
python3 - "$LIST_OUTPUT_FILE" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1]))
    print(len(data))
except Exception:
    print(0)
PY
)"
if [ -n "$ENRICHER_COUNT" ] && [ "$ENRICHER_COUNT" -ge 60 ]; then
    pass "list-enrichers returned $ENRICHER_COUNT enrichers"
else
    echo "  output: $(cat "$LIST_OUTPUT_FILE")"
    fail "list-enrichers: expected at least 60 enrichers, got $ENRICHER_COUNT"
fi
if cat "$LIST_OUTPUT_FILE" | grep -q "domain_to_whois" && cat "$LIST_OUTPUT_FILE" | grep -q "ip_to_asn" && cat "$LIST_OUTPUT_FILE" | grep -q "website_to_scrapling" && cat "$LIST_OUTPUT_FILE" | grep -q "social-confidence-aggregator"; then
    pass "list-enrichers includes representative existing enrichers"
else
    echo "  output: $(cat "$LIST_OUTPUT_FILE")"
    fail "list-enrichers: expected representative existing enrichers in output"
fi
echo ""

echo "--- CLI help discoverability ---"
LAUNCH_ENRICHER_HELP=$("$CLI" launch-enricher --help 2>&1) || { echo "$LAUNCH_ENRICHER_HELP"; fail "launch-enricher --help exit code"; }
if echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--node-ids-csv" && \
   echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--node-ids" && \
   echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--enable-http2" && \
   echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--enable-gpu" && \
   echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--gpu-provider" && \
   echo "$LAUNCH_ENRICHER_HELP" | grep -q -- "--launch-params-json"; then
    pass "launch-enricher help includes acceleration flags"
else
    echo "  output: $LAUNCH_ENRICHER_HELP"
    fail "launch-enricher help missing --node-ids/--node-ids-csv"
fi

LAUNCH_ENRICHERS_HELP=$("$CLI" launch-enrichers --help 2>&1) || { echo "$LAUNCH_ENRICHERS_HELP"; fail "launch-enrichers --help exit code"; }
if echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--node-ids-csv" && \
   echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--node-ids" && \
   echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--enable-http2" && \
   echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--enable-gpu" && \
   echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--gpu-provider" && \
   echo "$LAUNCH_ENRICHERS_HELP" | grep -q -- "--launch-params-json"; then
    pass "launch-enrichers help includes acceleration flags"
else
    echo "  output: $LAUNCH_ENRICHERS_HELP"
    fail "launch-enrichers help missing --node-ids/--node-ids-csv"
fi
echo ""

echo "--- launch-enrichers (multi) ---"
OUTPUT=$("$CLI" launch-enrichers --enricher-names "$SAMPLE_ENRICHERS" --node-ids "node-1,node-2" --sketch-id "sk-123" --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "launch-enrichers exit code"; }
if echo "$OUTPUT" | grep -q "\"enricher_name\": \"domain_to_whois\"" && echo "$OUTPUT" | grep -q "\"enricher_name\": \"ip_to_asn\"" && echo "$OUTPUT" | grep -q "\"enricher_name\": \"website_to_scrapling\""; then
    pass "launch-enrichers accepted multiple enrichers"
else
    echo "  output: $OUTPUT"
    fail "launch-enrichers: expected multiple known enricher names in output"
fi
echo ""

echo "--- launch-enricher (single) ---"
OUTPUT=$("$CLI" launch-enricher --enricher-name "domain_to_whois" --node-ids "node-1" --sketch-id "sk-123" --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "launch-enricher exit code"; }
if echo "$OUTPUT" | grep -q "\"id\": \"job-domain_to_whois\"" && echo "$OUTPUT" | grep -q "\"status\": \"queued\""; then
    pass "launch-enricher executed one enricher"
else
    echo "  output: $OUTPUT"
    fail "launch-enricher: expected job payload in output"
fi
echo ""

echo "--- launch-enricher (single, acceleration params) ---"
OUTPUT=$("$CLI" launch-enricher --enricher-name "website_to_text" --node-ids "node-1" --sketch-id "sk-123" --api-token "token-123" --enable-quic true --enable-gpu true --gpu-provider "auto" --max-concurrency 9 --request-timeout 12 --launch-params-json '{"headless":false}' 2>&1) || { echo "$OUTPUT"; fail "launch-enricher with params exit code"; }
if echo "$OUTPUT" | grep -q "\"id\": \"job-website_to_text\"" && echo "$OUTPUT" | grep -q "\"status\": \"queued\""; then
  pass "launch-enricher supports acceleration params"
else
  fail "launch-enricher acceleration params did not return queued job"
fi
ACCEL_PARAMS="$(python3 - <<'PY'
import json
import urllib.request

url = "http://127.0.0.1:%s/__requests" % int(__import__("os").environ["FLOWSINT_STUB_PORT"])
with urllib.request.urlopen(url, timeout=2) as response:
    requests = json.load(response)
if not requests:
    print("")
    raise SystemExit
for req in reversed(requests):
    if req["path"].endswith("/api/enrichers/website_to_text/launch"):
        print(json.dumps(req["body"].get("params", {}), sort_keys=True))
        break
else:
    print("")
PY
)" || { fail "launch request capture failed for acceleration params"; }
if [ "$ACCEL_PARAMS" != "" ] && echo "$ACCEL_PARAMS" | grep -q '"enable_quic": true' && echo "$ACCEL_PARAMS" | grep -q '"enable_gpu": true' && echo "$ACCEL_PARAMS" | grep -q '"gpu_provider": "auto"' && echo "$ACCEL_PARAMS" | grep -q '"max_concurrency": 9' && echo "$ACCEL_PARAMS" | grep -q '"request_timeout": 12' && echo "$ACCEL_PARAMS" | grep -q '"headless": false'; then
  pass "accelerated launch request included expected params"
else
  fail "accelerated launch request missing expected params: $ACCEL_PARAMS"
fi
echo ""

echo "--- launch-enricher (single, csv input) ---"
OUTPUT=$("$CLI" launch-enricher --enricher-name "domain_to_whois" --node-ids "" --node-ids-csv "$NODE_IDS_CSV_PATH" --sketch-id "sk-123" --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "launch-enricher csv exit code"; }
if echo "$OUTPUT" | grep -q "\"id\": \"job-domain_to_whois\"" && echo "$OUTPUT" | grep -q "\"status\": \"queued\""; then
    pass "launch-enricher accepted --node-ids-csv"
else
    echo "  output: $OUTPUT"
    fail "launch-enricher csv: expected job payload in output"
fi
LAST_REQUEST="$(python3 - <<'PY'
import json
import os
import urllib.request

requests = json.loads(urllib.request.urlopen(f"{os.environ['FLOWSINT_API_URL']}/__requests").read())
body = requests[-1].get('body', {})
print(','.join(body.get('node_ids', [])))
PY
)"
if [ "$LAST_REQUEST" = "csv-node-1,csv-node-2" ]; then
    pass "launch-enricher csv request body contains csv node IDs"
else
    echo "  last request body: $LAST_REQUEST"
    fail "launch-enricher csv: expected csv-node-1,csv-node-2"
fi
echo ""

echo "--- launch-enrichers (multi, csv input) ---"
OUTPUT=$("$CLI" launch-enrichers --enricher-names "$SAMPLE_ENRICHERS" --node-ids "" --node-ids-csv "$NODE_IDS_CSV_PATH" --sketch-id "sk-123" --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "launch-enrichers csv exit code"; }
if echo "$OUTPUT" | grep -q "\"enricher_name\": \"domain_to_whois\"" && echo "$OUTPUT" | grep -q "\"enricher_name\": \"ip_to_asn\"" && echo "$OUTPUT" | grep -q "\"enricher_name\": \"website_to_scrapling\""; then
    pass "launch-enrichers accepted --node-ids-csv"
else
    echo "  output: $OUTPUT"
    fail "launch-enrichers csv: expected enricher names in output"
fi
LAST_REQUESTS="$(python3 - <<'PY'
import json
import os
import urllib.request

requests = json.loads(urllib.request.urlopen(f"{os.environ['FLOWSINT_API_URL']}/__requests").read())
launches = [
    entry for entry in requests
    if entry.get('path', '').startswith('/api/enrichers/') and entry.get('path', '').endswith('/launch')
]
if launches:
    ids = launches[-1]['body'].get('node_ids', [])
    print(','.join(ids))
else:
    print('')
PY
)"
if [ "$LAST_REQUESTS" = "csv-node-1,csv-node-2" ]; then
    pass "launch-enrichers csv request body contains csv node IDs"
else
    echo "  last request body: $LAST_REQUESTS"
    fail "launch-enrichers csv: expected csv-node-1,csv-node-2"
fi
echo ""

echo "--- list-investigations ---"
OUTPUT=$("$CLI" list-investigations 2>&1) || { echo "$OUTPUT"; fail "list-investigations exit code"; }
if echo "$OUTPUT" | grep -q "Test Investigation"; then
    pass "list-investigations output contains 'Test Investigation'"
else
    echo "  output: $OUTPUT"
    fail "list-investigations: expected 'Test Investigation' in output"
fi
echo ""

echo "--- create-enricher-template ---"
OUTPUT=$("$CLI" create-enricher-template \
  --name "CLI Template" \
  --category "test" \
  --description "Created from e2e" \
  --content '{"name":"cli-template","category":"test","version":1.0,"input":{"type":"string"},"request":{"method":"GET","url":"https://example.com/{value}"},"output":{"type":"json"},"response":{"expect":"json"}}' \
  --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "create-enricher-template exit code"; }
if echo "$OUTPUT" | grep -q "cli-created-template"; then
    pass "create-enricher-template output contains 'cli-created-template'"
else
    echo "  output: $OUTPUT"
    fail "create-enricher-template: expected 'cli-created-template' in output"
fi
echo ""

echo "--- create-enricher-template (template mode) ---"
if [ ! -d "$TEMPLATE_PATH" ]; then
    echo "  fixture template dir not found: $TEMPLATE_PATH"
    fail "create-enricher-template template mode: fixture template missing"
else
    OUTPUT=$("$CLI" create-enricher-template \
      --name "Cookied Template" \
      --category "test" \
      --description "Created from cookiecutter mode" \
      --template-path "$TEMPLATE_PATH" \
      --template-context "$TEMPLATE_CONTEXT" \
      --api-token "token-123" 2>&1) || { echo "$OUTPUT"; fail "create-enricher-template template mode exit code"; }
if echo "$OUTPUT" | grep -q "\"name\": \"rendered-template\""; then
    pass "create-enricher-template template mode output contains rendered template name"
else
    echo "  output: $OUTPUT"
    fail "create-enricher-template template mode: expected rendered fields in output"
fi
# Also assert the API POST payload (via /__requests) includes rendered template fields
TEMPLATE_REQUEST_PAYLOAD="$(python3 - <<'PY'
import json, os, urllib.request
requests = json.loads(urllib.request.urlopen(f"{os.environ['FLOWSINT_API_URL']}/__requests").read())
template_reqs = [
    e for e in requests
    if e.get('path', '') == '/api/enrichers/templates' and e.get('method') == 'POST'
]
if template_reqs:
    body = template_reqs[-1].get('body', {})
    content = body.get('content', {}) if isinstance(body, dict) else {}
    print(json.dumps(content))
else:
    print('{}')
PY
)"
if echo "$TEMPLATE_REQUEST_PAYLOAD" | grep -q "\"name\": \"rendered-template\"" && \
   echo "$TEMPLATE_REQUEST_PAYLOAD" | grep -q "\"category\": \"test\"" && \
   echo "$TEMPLATE_REQUEST_PAYLOAD" | grep -q "\"method\": \"GET\"" && \
   echo "$TEMPLATE_REQUEST_PAYLOAD" | grep -q "https://dns.example.com/{value}"; then
    pass "template mode POST payload includes rendered template fields"
else
    echo "  template request body content: $TEMPLATE_REQUEST_PAYLOAD"
    fail "create-enricher-template template mode: expected rendered fields in POST payload"
fi
fi
echo ""

echo "--- login ---"
OUTPUT=$("$CLI" login --email test@test.com --password test 2>&1) || { echo "$OUTPUT"; fail "login exit code"; }
if echo "$OUTPUT" | grep -q "fake-jwt-token"; then
    pass "login output contains 'fake-jwt-token'"
else
    echo "  output: $OUTPUT"
    fail "login: expected 'fake-jwt-token' in output"
fi
echo ""

cleanup
trap - EXIT INT TERM

if [ "$FAILED" -eq 0 ]; then
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo "=== SOME TESTS FAILED ==="
    exit 1
fi
