#!/usr/bin/env bash
# sidecar-manager.sh — Manage the amplifier-openclaw JSON-RPC sidecar lifecycle
#
# Usage:
#   sidecar-manager.sh start   — Start sidecar if not running
#   sidecar-manager.sh stop    — Stop the sidecar
#   sidecar-manager.sh status  — Check if sidecar is running
#   sidecar-manager.sh restart — Stop then start
#   sidecar-manager.sh send    — Send a JSON-RPC request (reads from stdin, writes to stdout)
#
# Environment:
#   AMPLIFIER_IDLE_TIMEOUT  — Seconds before idle shutdown (default: 300)
#   AMPLIFIER_SIDECAR_DIR   — State directory (default: ~/.openclaw/amplifier)

set -euo pipefail

SIDECAR_DIR="${AMPLIFIER_SIDECAR_DIR:-$HOME/.openclaw/amplifier}"
PID_FILE="$SIDECAR_DIR/sidecar.pid"
FIFO_IN="$SIDECAR_DIR/sidecar.in"
FIFO_OUT="$SIDECAR_DIR/sidecar.out"
LOG_FILE="$SIDECAR_DIR/sidecar.log"
IDLE_TIMEOUT="${AMPLIFIER_IDLE_TIMEOUT:-300}"

mkdir -p "$SIDECAR_DIR"

_is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # Stale PID file
        rm -f "$PID_FILE"
    fi
    return 1
}

_cleanup_fifos() {
    rm -f "$FIFO_IN" "$FIFO_OUT"
}

cmd_start() {
    if _is_running; then
        echo '{"status":"already_running","pid":'$(cat "$PID_FILE")'}'
        return 0
    fi

    _cleanup_fifos
    mkfifo "$FIFO_IN"
    mkfifo "$FIFO_OUT"

    # Start sidecar: stdin from FIFO_IN, stdout to FIFO_OUT, stderr to log
    amplifier-openclaw serve < "$FIFO_IN" > "$FIFO_OUT" 2>>"$LOG_FILE" &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    # Open a persistent writer to FIFO_IN so the pipe stays open
    exec 3>"$FIFO_IN"

    # Wait for bridge/ready notification (up to 10s)
    local ready=""
    if timeout 10 head -n1 "$FIFO_OUT" | grep -q "bridge/ready"; then
        ready="true"
    fi

    if _is_running; then
        echo '{"status":"started","pid":'"$pid"',"ready":'"${ready:-false}"'}'
    else
        _cleanup_fifos
        echo '{"status":"failed","error":"Sidecar exited immediately. Check '"$LOG_FILE"'"}'
        return 1
    fi
}

cmd_stop() {
    if ! _is_running; then
        echo '{"status":"not_running"}'
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")

    # Send shutdown request via FIFO if open, otherwise just kill
    if [ -p "$FIFO_IN" ]; then
        echo '{"jsonrpc":"2.0","method":"bridge/shutdown","params":{}}' > "$FIFO_IN" 2>/dev/null || true
        # Give it a moment to exit gracefully
        sleep 1
    fi

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 0.5
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    _cleanup_fifos
    # Close FD 3 if open
    exec 3>&- 2>/dev/null || true
    echo '{"status":"stopped"}'
}

cmd_status() {
    if _is_running; then
        echo '{"status":"running","pid":'$(cat "$PID_FILE")'}'
    else
        echo '{"status":"stopped"}'
    fi
}

cmd_restart() {
    cmd_stop >/dev/null
    cmd_start
}

cmd_send() {
    # Read JSON-RPC request from stdin, send to sidecar, return response
    if ! _is_running; then
        echo '{"error":"sidecar_not_running","message":"Start sidecar first with: sidecar-manager.sh start"}' >&2
        return 1
    fi

    local request
    request=$(cat)

    # Write request to sidecar's stdin FIFO
    echo "$request" > "$FIFO_IN"

    # Read one line of response from sidecar's stdout FIFO
    timeout 60 head -n1 "$FIFO_OUT"
}

# --- Main ---
case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    send)    cmd_send ;;
    help|--help|-h)
        echo "Usage: $0 {start|stop|status|restart|send}"
        echo ""
        echo "Manages the amplifier-openclaw JSON-RPC sidecar."
        echo "  start   — Launch sidecar if not running"
        echo "  stop    — Gracefully stop the sidecar"
        echo "  status  — Check if sidecar is running (JSON output)"
        echo "  restart — Stop then start"
        echo "  send    — Pipe JSON-RPC request from stdin, get response on stdout"
        ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Usage: $0 {start|stop|status|restart|send}" >&2
        exit 1
        ;;
esac
