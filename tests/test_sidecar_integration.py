"""Integration tests for the amplifier-openclaw sidecar lifecycle.

Starts ``amplifier-openclaw serve`` as a subprocess and exercises the
JSON-RPC protocol: bridge/ready, session/list, bundle/list,
augment/evaluate_tool, augment/cost_report, and bridge/shutdown.

No API keys required — these tests verify the RPC protocol and sidecar
lifecycle only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMEOUT = 10  # seconds


def _start_sidecar() -> subprocess.Popen:
    """Launch ``amplifier-openclaw serve`` as a subprocess."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from amplifier_app_openclaw.cli import cli; cli(['serve'])"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc


def _read_line(proc: subprocess.Popen, timeout: float = _TIMEOUT) -> dict[str, Any]:
    """Read one newline-delimited JSON-RPC message from the sidecar stdout."""
    assert proc.stdout is not None
    # Use a simple deadline loop since stdout.readline() blocks
    import selectors

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    events = sel.select(timeout=timeout)
    sel.close()
    if not events:
        raise TimeoutError("No output from sidecar within timeout")
    line = proc.stdout.readline()
    if not line:
        raise EOFError("Sidecar stdout closed")
    return json.loads(line)


def _send_request(proc: subprocess.Popen, rid: int, method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC request to the sidecar stdin."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _send_notification(proc: subprocess.Popen, method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC notification (no id) to the sidecar."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSidecarIntegration:
    """End-to-end sidecar lifecycle tests."""

    def test_bridge_ready_on_startup(self):
        """Sidecar emits bridge/ready immediately on startup."""
        proc = _start_sidecar()
        try:
            msg = _read_line(proc)
            assert msg["method"] == "bridge/ready"
            assert "version" in msg.get("params", {})
            assert "pid" in msg.get("params", {})
        finally:
            proc.kill()
            proc.wait()

    def test_session_list_empty(self):
        """session/list returns an empty list when no sessions exist."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 1, "session/list")
            resp = _read_line(proc)
            assert resp["id"] == 1
            result = resp["result"]
            # Result may be a list or a dict with a "sessions" key
            sessions = result if isinstance(result, list) else result.get("sessions", [])
            assert isinstance(sessions, list)
            assert len(sessions) == 0
        finally:
            proc.kill()
            proc.wait()

    def test_bundle_list(self):
        """bundle/list returns an array of bundles."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 2, "bundle/list")
            resp = _read_line(proc)
            assert resp["id"] == 2
            result = resp["result"]
            # Result may be a list or a dict with a "bundles" key
            bundles = result if isinstance(result, list) else result.get("bundles", [])
            assert isinstance(bundles, list)
        finally:
            proc.kill()
            proc.wait()

    def test_evaluate_tool_continue(self):
        """augment/evaluate_tool allows safe commands (ls -la)."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 3, "augment/evaluate_tool", {
                "tool": "shell",
                "input": "ls -la",
            })
            resp = _read_line(proc)
            assert resp["id"] == 3
            result = resp["result"]
            assert result["action"] == "continue"
        finally:
            proc.kill()
            proc.wait()

    def test_evaluate_tool_deny(self):
        """augment/evaluate_tool denies dangerous commands (sudo rm -rf /)."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 4, "augment/evaluate_tool", {
                "tool": "shell",
                "input": "sudo rm -rf /",
            })
            resp = _read_line(proc)
            assert resp["id"] == 4
            result = resp["result"]
            assert result["action"] == "deny"
        finally:
            proc.kill()
            proc.wait()

    def test_cost_report(self):
        """augment/cost_report returns a valid report structure."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 5, "augment/cost_report", {"period": "day"})
            resp = _read_line(proc)
            assert resp["id"] == 5
            result = resp["result"]
            assert "total_cost" in result or "entries" in result or "period" in result
        finally:
            proc.kill()
            proc.wait()

    def test_bridge_shutdown_clean_exit(self):
        """bridge/shutdown causes the sidecar to exit cleanly."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_notification(proc, "bridge/shutdown")
            # Should exit within a few seconds
            exit_code = proc.wait(timeout=_TIMEOUT)
            assert exit_code == 0
        except Exception:
            proc.kill()
            proc.wait()
            raise

    def test_unknown_method_returns_error(self):
        """Unknown RPC methods return METHOD_NOT_FOUND error."""
        proc = _start_sidecar()
        try:
            _read_line(proc)  # consume bridge/ready
            _send_request(proc, 99, "nonexistent/method")
            resp = _read_line(proc)
            assert resp["id"] == 99
            assert "error" in resp
            assert resp["error"]["code"] == -32601  # METHOD_NOT_FOUND
        finally:
            proc.kill()
            proc.wait()
