#!/usr/bin/env bash
# smoke_pi.sh — Smoke test for amplifier-openclaw on Raspberry Pi.
#
# Measures startup time, memory usage, and CLI command timing.
# Checks all exit codes and reports pass/fail.

set -euo pipefail

PASS=0
FAIL=0
CMD="amplifier-openclaw"

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }

check() {
    local label="$1"; shift
    local start end elapsed
    start=$(date +%s%N)
    if "$@" >/dev/null 2>&1; then
        end=$(date +%s%N)
        elapsed=$(( (end - start) / 1000000 ))
        green "  PASS  ${label} (${elapsed}ms)"
        PASS=$((PASS + 1))
    else
        end=$(date +%s%N)
        elapsed=$(( (end - start) / 1000000 ))
        red "  FAIL  ${label} (${elapsed}ms, exit=$?)"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== amplifier-openclaw Pi smoke test ==="
echo ""

# 1. --version
check "--version" $CMD --version

# 2. bundles list
check "bundles list" $CMD bundles list

# 3. cost report
check "cost --period=day" $CMD cost --period day

# 4. Sidecar startup + shutdown timing
echo ""
echo "--- Sidecar startup/shutdown ---"
START=$(date +%s%N)
echo '{"jsonrpc":"2.0","method":"bridge/shutdown"}' | timeout 10 $CMD serve >/dev/null 2>&1 || true
END=$(date +%s%N)
STARTUP_MS=$(( (END - START) / 1000000 ))
echo "  Sidecar start→shutdown: ${STARTUP_MS}ms"

# 5. Memory usage (peak RSS via /usr/bin/time if available)
echo ""
echo "--- Memory usage ---"
if command -v /usr/bin/time >/dev/null 2>&1; then
    MEM=$( { echo '{"jsonrpc":"2.0","method":"bridge/shutdown"}' | /usr/bin/time -v timeout 10 $CMD serve; } 2>&1 | grep "Maximum resident" | awk '{print $NF}' || echo "N/A" )
    echo "  Peak RSS: ${MEM} KB"
else
    echo "  /usr/bin/time not available, skipping memory measurement"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
