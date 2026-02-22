#!/usr/bin/env bash
# sidecar-manager.sh — Manage the amplifier-openclaw JSON-RPC sidecar
#
# Usage:
#   sidecar-manager.sh start   — Start sidecar (Unix socket mode)
#   sidecar-manager.sh stop    — Stop the sidecar
#   sidecar-manager.sh status  — Check if running
#   sidecar-manager.sh restart — Stop + start
#   sidecar-manager.sh send    — Send JSON-RPC request (stdin or arg), get response

set -euo pipefail

SIDECAR_DIR="${AMPLIFIER_SIDECAR_DIR:-$HOME/.openclaw/amplifier}"
SOCK="$SIDECAR_DIR/sidecar.sock"
PID_FILE="$SIDECAR_DIR/sidecar.pid"
LOG_FILE="$SIDECAR_DIR/sidecar.log"

mkdir -p "$SIDECAR_DIR"

_is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

cmd_start() {
    if _is_running; then
        echo '{"status":"already_running","pid":'$(cat "$PID_FILE")'}'
        return 0
    fi

    rm -f "$SOCK" "$PID_FILE"

    # Start sidecar in background with Unix socket mode
    nohup amplifier-openclaw serve --socket "$SOCK" \
        >> "$LOG_FILE" 2>&1 &
    local launcher_pid=$!

    # Wait for PID file (written by the sidecar itself) or socket
    for i in $(seq 1 30); do
        if [ -S "$SOCK" ]; then
            break
        fi
        sleep 0.5
    done

    if [ -S "$SOCK" ] && [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        echo '{"status":"started","pid":'"$pid"',"socket":"'"$SOCK"'"}'
    else
        echo '{"status":"failed","error":"Sidecar did not start. Check '"$LOG_FILE"'"}'
        kill "$launcher_pid" 2>/dev/null || true
        return 1
    fi
}

cmd_stop() {
    if ! _is_running; then
        echo '{"status":"not_running"}'
        rm -f "$PID_FILE" "$SOCK"
        return 0
    fi

    local pid=$(cat "$PID_FILE")

    # Graceful: send shutdown through socket
    if [ -S "$SOCK" ]; then
        echo '{"jsonrpc":"2.0","method":"bridge/shutdown","params":{}}' | \
            timeout 3 socat - UNIX-CONNECT:"$SOCK" 2>/dev/null || true
        sleep 1
    fi

    # Force if still running
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 0.5
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE" "$SOCK"
    echo '{"status":"stopped"}'
}

cmd_status() {
    if _is_running && [ -S "$SOCK" ]; then
        echo '{"status":"running","pid":'$(cat "$PID_FILE")',"socket":"'"$SOCK"'"}'
    else
        rm -f "$PID_FILE"
        echo '{"status":"stopped"}'
    fi
}

cmd_restart() {
    cmd_stop >/dev/null 2>&1
    sleep 1
    cmd_start
}

cmd_send() {
    # Auto-start if not running
    if ! _is_running || [ ! -S "$SOCK" ]; then
        cmd_start >/dev/null 2>&1
        sleep 1
    fi

    if [ ! -S "$SOCK" ]; then
        echo '{"error":"sidecar_unavailable"}' >&2
        return 1
    fi

    local request="${1:-$(cat)}"
    echo "$request" | timeout 120 socat - UNIX-CONNECT:"$SOCK" 2>/dev/null
}

case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    send)    shift; cmd_send "$@" ;;
    help|--help|-h)
        echo "Usage: $0 {start|stop|status|restart|send [request]}"
        ;;
    *)
        echo "Unknown command: $1" >&2
        exit 1
        ;;
esac
