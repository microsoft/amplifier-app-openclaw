#!/usr/bin/env bash
# amplifier-tool.sh — OpenClaw agent wrapper for Amplifier operations
#
# Usage:
#   amplifier-tool.sh delegate [--bundle NAME] [--timeout SECS] "prompt"
#   amplifier-tool.sh bundles [--root-only]
#   amplifier-tool.sh cost [--session ID] [--period PERIOD]
#   amplifier-tool.sh evaluate "tool_call_json"
#   amplifier-tool.sh recipe "recipe_name" [params...]
#
# This script uses the CLI directly (Phase 0). For Phase 1 sidecar mode,
# it can optionally route through the sidecar if running.
#
# Environment:
#   AMPLIFIER_USE_SIDECAR=1  — Prefer sidecar over direct CLI (Phase 1)
#   AMPLIFIER_SIDECAR_DIR    — Sidecar state dir (default: ~/.openclaw/amplifier)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_MGR="$SCRIPT_DIR/sidecar-manager.sh"
USE_SIDECAR="${AMPLIFIER_USE_SIDECAR:-0}"

# --- Helpers ---

_rpc_id() {
    echo $((RANDOM * RANDOM))
}

_via_sidecar() {
    local method="$1"
    local params="$2"
    local id
    id=$(_rpc_id)

    # Ensure sidecar is running
    local status
    status=$("$SIDECAR_MGR" status)
    if echo "$status" | grep -q '"stopped"'; then
        "$SIDECAR_MGR" start >/dev/null 2>&1
    fi

    local request='{"jsonrpc":"2.0","id":'"$id"',"method":"'"$method"'","params":'"$params"'}'
    echo "$request" | "$SIDECAR_MGR" send
}

# --- Commands ---

cmd_delegate() {
    local bundle="foundation"
    local timeout=300
    local cwd="."
    local prompt=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --bundle)  bundle="$2"; shift 2 ;;
            --timeout) timeout="$2"; shift 2 ;;
            --cwd)     cwd="$2"; shift 2 ;;
            *)         prompt="$1"; shift ;;
        esac
    done

    if [ -z "$prompt" ]; then
        echo '{"error":"missing_prompt","message":"Usage: amplifier-tool.sh delegate [--bundle NAME] \"prompt\""}' >&2
        exit 1
    fi

    if [ "$USE_SIDECAR" = "1" ]; then
        _via_sidecar "session/execute" '{"prompt":"'"$(echo "$prompt" | sed 's/"/\\"/g')"'","bundle":"'"$bundle"'","cwd":"'"$cwd"'","timeout":'"$timeout"'}'
    else
        # Phase 0: direct CLI invocation
        amplifier-openclaw run --bundle "$bundle" --cwd "$cwd" --timeout "$timeout" "$prompt"
    fi
}

cmd_bundles() {
    local root_only=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --root-only) root_only="--root-only"; shift ;;
            *) shift ;;
        esac
    done

    if [ "$USE_SIDECAR" = "1" ]; then
        _via_sidecar "bundles/list" '{"root_only":'"${root_only:+true}"'${root_only:-false}}'
    else
        amplifier-openclaw bundles list $root_only
    fi
}

cmd_cost() {
    local session=""
    local period=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --session) session="$2"; shift 2 ;;
            --period)  period="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    local args=""
    [ -n "$session" ] && args="$args --session $session"
    [ -n "$period" ]  && args="$args --period $period"

    if [ "$USE_SIDECAR" = "1" ]; then
        local params='{}'
        [ -n "$session" ] && params='{"session_id":"'"$session"'"}'
        [ -n "$period" ]  && params='{"period":"'"$period"'"}'
        _via_sidecar "cost/summary" "$params"
    else
        amplifier-openclaw cost $args
    fi
}

cmd_evaluate() {
    local tool_call="$1"
    # Evaluation only available via sidecar (Phase 1)
    if [ "$USE_SIDECAR" != "1" ]; then
        echo '{"error":"sidecar_required","message":"Evaluate requires Phase 1 sidecar mode. Set AMPLIFIER_USE_SIDECAR=1"}'
        exit 1
    fi
    _via_sidecar "governance/evaluate" "$tool_call"
}

cmd_recipe() {
    local recipe_name="$1"; shift
    local params="${1:-{}}"

    if [ "$USE_SIDECAR" = "1" ]; then
        _via_sidecar "recipe/execute" '{"recipe":"'"$recipe_name"'","params":'"$params"'}'
    else
        echo '{"error":"sidecar_required","message":"Recipes require Phase 1 sidecar mode. Set AMPLIFIER_USE_SIDECAR=1"}'
        exit 1
    fi
}

# --- Main ---
case "${1:-help}" in
    delegate)  shift; cmd_delegate "$@" ;;
    bundles)   shift; cmd_bundles "$@" ;;
    cost)      shift; cmd_cost "$@" ;;
    evaluate)  shift; cmd_evaluate "$@" ;;
    recipe)    shift; cmd_recipe "$@" ;;
    help|--help|-h)
        cat <<'EOF'
amplifier-tool.sh — Amplifier integration for OpenClaw

Commands:
  delegate [--bundle NAME] [--timeout S] "prompt"  — Delegate task to Amplifier
  bundles [--root-only]                             — List available bundles
  cost [--session ID] [--period PERIOD]             — Show cost report
  evaluate "tool_call_json"                         — Evaluate tool call (Phase 1)
  recipe "name" [params_json]                       — Run automation recipe (Phase 1)

Examples:
  amplifier-tool.sh delegate "Research local-first AI"
  amplifier-tool.sh delegate --bundle python-dev "Build a REST API"
  amplifier-tool.sh bundles
  amplifier-tool.sh cost --period week

Environment:
  AMPLIFIER_USE_SIDECAR=1  — Use Phase 1 sidecar instead of direct CLI
EOF
        ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Run '$0 help' for usage." >&2
        exit 1
        ;;
esac
