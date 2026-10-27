#!/usr/bin/env bash
# SplitMate one-command smoke test.
#
#   bash scripts/smoke.sh          (or: make smoke)
#
# 1. Runs the full unittest suite.
# 2. Starts a local fake PayPal + fake LLM server (no network, no accounts).
# 3. Starts two SplitMate instances:
#      - mock mode  (fully offline demo path)
#      - real mode  (real PayPal/LLM HTTP clients against the fake server)
# 4. Drives both end-to-end with a scripted client and asserts the results.
#
# Nothing outside this folder is touched and no real credentials are needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
FAKE_PORT="${SMOKE_FAKE_PORT:-8899}"
MOCK_PORT="${SMOKE_MOCK_PORT:-8010}"
REAL_PORT="${SMOKE_REAL_PORT:-8011}"
LOG_DIR="${TMPDIR:-/tmp}/splitmate-smoke"
rm -rf "$LOG_DIR" 2>/dev/null || true
mkdir -p "$LOG_DIR"

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {
  local url="$1" name="$2"
  for _ in $(seq 1 100); do
    if curl -sf --max-time 1 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  echo "FAIL: timed out waiting for $name ($url)" >&2
  return 1
}

echo "==> 1/4 Running the unittest suite"
"$PYTHON" -m unittest discover -s tests -t . -q

echo "==> 2/4 Starting the fake PayPal + LLM server on 127.0.0.1:${FAKE_PORT}"
"$PYTHON" -m splitmate.fake_services --port "$FAKE_PORT" >"$LOG_DIR/fake.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:${FAKE_PORT}/healthz" "fake services"

echo "==> 3/4 Starting SplitMate mock mode on 127.0.0.1:${MOCK_PORT}"
SPLITMATE_MODE=mock \
SPLITMATE_PORT="$MOCK_PORT" \
SPLITMATE_PUBLIC_BASE_URL="http://127.0.0.1:${MOCK_PORT}" \
  "$PYTHON" app.py --quiet >"$LOG_DIR/mock.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:${MOCK_PORT}/api/health" "mock app"

echo "==> 4/4 Starting SplitMate real mode against the fake server on 127.0.0.1:${REAL_PORT}"
SPLITMATE_MODE=real \
SPLITMATE_PORT="$REAL_PORT" \
SPLITMATE_PUBLIC_BASE_URL="http://127.0.0.1:${REAL_PORT}" \
PAYPAL_BASE_URL="http://127.0.0.1:${FAKE_PORT}" \
PAYPAL_CLIENT_ID="smoke-client-id" \
PAYPAL_CLIENT_SECRET="smoke-client-secret" \
LLM_API_KEY="smoke-api-key" \
LLM_BASE_URL="http://127.0.0.1:${FAKE_PORT}/v1" \
LLM_MODEL="fake-model" \
  "$PYTHON" app.py --quiet >"$LOG_DIR/real.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:${REAL_PORT}/api/health" "real-mode app"

echo "--> Driving both instances end-to-end"
"$PYTHON" scripts/smoke_client.py \
  --mock-base "http://127.0.0.1:${MOCK_PORT}" \
  --real-base "http://127.0.0.1:${REAL_PORT}" \
  --fake-base "http://127.0.0.1:${FAKE_PORT}" \
  --plan-file "$ROOT/samples/demo-plan.txt"

echo
echo "SMOKE PASS: mock-mode end-to-end + real-mode PayPal/LLM HTTP path verified."
