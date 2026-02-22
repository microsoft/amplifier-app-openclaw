"""Tests for adapter modules (display, approval, streaming, spawn)."""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.rpc import JsonRpcWriter


# ---------------------------------------------------------------------------
# DisplaySystem
# ---------------------------------------------------------------------------

class TestDisplaySystem:
    def setup_method(self):
        self.buf = io.StringIO()
        self.writer = JsonRpcWriter(self.buf)

    def _last_msg(self):
        return json.loads(self.buf.getvalue().strip().split("\n")[-1])

    def test_show_message_sends_notification(self):
        from amplifier_app_openclaw.adapters.display import OpenClawDisplaySystem
        ds = OpenClawDisplaySystem("sess-1", self.writer)
        ds.show_message("Hello world")
        msg = self._last_msg()
        assert msg["method"] == "session/display"
        assert "id" not in msg  # notification
        assert msg["params"]["session_id"] == "sess-1"
        assert msg["params"]["message"] == "Hello world"
        assert msg["params"]["level"] == "info"

    def test_show_message_custom_level(self):
        from amplifier_app_openclaw.adapters.display import OpenClawDisplaySystem
        ds = OpenClawDisplaySystem("s2", self.writer)
        ds.show_message("Error!", level="error", source="agent")
        msg = self._last_msg()
        assert msg["params"]["level"] == "error"
        assert msg["params"]["source"] == "agent"


# ---------------------------------------------------------------------------
# ApprovalSystem
# ---------------------------------------------------------------------------

class TestApprovalSystem:
    def setup_method(self):
        self.buf = io.StringIO()
        self.writer = JsonRpcWriter(self.buf)

    @pytest.mark.asyncio
    async def test_approval_resolved(self):
        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        ap = OpenClawApprovalSystem("sess-1", self.writer)

        async def resolve_soon():
            await asyncio.sleep(0.02)
            msg = json.loads(self.buf.getvalue().strip())
            req_id = msg["params"]["request_id"]
            ap.resolve_approval(req_id, "allow")

        asyncio.create_task(resolve_soon())
        result = await ap.request_approval("Delete file?", ["allow", "deny"], timeout=5, default="deny")
        assert result == "allow"

    @pytest.mark.asyncio
    async def test_approval_timeout_returns_default(self):
        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        ap = OpenClawApprovalSystem("sess-1", self.writer)
        result = await ap.request_approval("Delete?", ["allow", "deny"], timeout=0.05, default="deny")
        assert result == "deny"

    @pytest.mark.asyncio
    async def test_concurrent_approvals(self):
        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        ap = OpenClawApprovalSystem("sess-1", self.writer)

        async def resolve_all():
            await asyncio.sleep(0.02)
            lines = self.buf.getvalue().strip().split("\n")
            for line in lines:
                msg = json.loads(line)
                req_id = msg["params"]["request_id"]
                ap.resolve_approval(req_id, "allow")

        t1 = asyncio.create_task(ap.request_approval("Q1?", ["allow", "deny"], timeout=5, default="deny"))
        t2 = asyncio.create_task(ap.request_approval("Q2?", ["allow", "deny"], timeout=5, default="deny"))
        await asyncio.sleep(0.01)
        asyncio.create_task(resolve_all())

        r1, r2 = await asyncio.gather(t1, t2)
        assert r1 == "allow"
        assert r2 == "allow"

    @pytest.mark.asyncio
    async def test_resolve_unknown_request_ignored(self):
        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        ap = OpenClawApprovalSystem("sess-1", self.writer)
        # Should not raise
        ap.resolve_approval("nonexistent", "allow")

    @pytest.mark.asyncio
    async def test_approval_notification_format(self):
        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        ap = OpenClawApprovalSystem("sess-1", self.writer)

        async def resolve_soon():
            await asyncio.sleep(0.02)
            msg = json.loads(self.buf.getvalue().strip())
            ap.resolve_approval(msg["params"]["request_id"], "deny")

        asyncio.create_task(resolve_soon())
        await ap.request_approval("OK?", ["yes", "no"], timeout=5, default="no")

        msg = json.loads(self.buf.getvalue().strip().split("\n")[0])
        assert msg["method"] == "session/approval"
        assert "id" not in msg
        assert msg["params"]["options"] == ["yes", "no"]
        assert msg["params"]["timeout"] == 5


# ---------------------------------------------------------------------------
# StreamingHook
# ---------------------------------------------------------------------------

class TestStreamingHook:
    def test_sanitize_truncates_long_strings(self):
        from amplifier_app_openclaw.adapters.streaming import _sanitize, _MAX_STR_LEN
        long_str = "x" * (_MAX_STR_LEN + 100)
        assert len(_sanitize(long_str)) == _MAX_STR_LEN

    def test_sanitize_caps_list_length(self):
        from amplifier_app_openclaw.adapters.streaming import _sanitize, _MAX_LIST_LEN
        big_list = list(range(_MAX_LIST_LEN + 50))
        assert len(_sanitize(big_list)) == _MAX_LIST_LEN

    def test_sanitize_nested(self):
        from amplifier_app_openclaw.adapters.streaming import _sanitize, _MAX_STR_LEN
        data = {"key": "y" * 20000, "nested": [{"inner": "z" * 20000}]}
        result = _sanitize(data)
        assert len(result["key"]) == _MAX_STR_LEN
        assert len(result["nested"][0]["inner"]) == _MAX_STR_LEN

    @pytest.mark.asyncio
    async def test_handler_returns_hook_result(self):
        from amplifier_app_openclaw.adapters.streaming import OpenClawStreamingHook
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        hook = OpenClawStreamingHook("sess-1", writer)
        handler = hook._make_handler("content_block:delta")
        result = await handler("content_block:delta", {"text": "hello"})
        # Must return HookResult
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_handler_sends_notification(self):
        from amplifier_app_openclaw.adapters.streaming import OpenClawStreamingHook
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        hook = OpenClawStreamingHook("sess-1", writer)
        handler = hook._make_handler("tool:pre")
        await handler("tool:pre", {"tool": "exec"})
        msg = json.loads(buf.getvalue().strip())
        assert msg["method"] == "session/event"
        assert msg["params"]["type"] == "tool:pre"
        assert msg["params"]["session_id"] == "sess-1"

    def test_register_and_unregister(self):
        from amplifier_app_openclaw.adapters.streaming import OpenClawStreamingHook, FORWARDED_EVENTS
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        hook = OpenClawStreamingHook("sess-1", writer)

        unreg_calls = []
        mock_hooks = MagicMock()
        mock_hooks.register.return_value = lambda: unreg_calls.append(1)

        mock_session = MagicMock()
        mock_session.coordinator.hooks = mock_hooks

        hook.register(mock_session)
        assert mock_hooks.register.call_count == len(FORWARDED_EVENTS)

        hook.unregister()
        assert len(unreg_calls) == len(FORWARDED_EVENTS)


# ---------------------------------------------------------------------------
# SpawnManager
# ---------------------------------------------------------------------------

class TestSpawnManager:
    def test_cli_spawn_raises(self):
        from amplifier_app_openclaw.spawn import CLISpawnManager

        async def _test():
            sm = CLISpawnManager(MagicMock())
            with pytest.raises(NotImplementedError):
                await sm.spawn()
            with pytest.raises(NotImplementedError):
                await sm.resume()

        asyncio.get_event_loop().run_until_complete(_test())

    @pytest.mark.asyncio
    async def test_openclaw_spawn_forwards_params(self):
        from amplifier_app_openclaw.spawn import OpenClawSpawnManager

        mock_prepared = MagicMock()
        mock_prepared.bundle = MagicMock()
        mock_prepared.spawn = AsyncMock(return_value={
            "output": "done",
            "session_id": "child-1",
            "status": "completed",
            "turn_count": 3,
            "metadata": {},
        })

        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        sm = OpenClawSpawnManager(mock_prepared, "parent-1", writer)

        config = {
            "instruction": "do stuff",
            "session_cwd": "/tmp",
            "compose": True,
        }

        with patch("amplifier_foundation.load_bundle", new=AsyncMock()):
            result = await sm.spawn(config)

        assert result["output"] == "done"
        mock_prepared.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_openclaw_resume_not_supported(self):
        from amplifier_app_openclaw.spawn import OpenClawSpawnManager
        mock_prepared = MagicMock()
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        sm = OpenClawSpawnManager(mock_prepared, "p1", writer)
        result = await sm.resume({"session_id": "old"})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_openclaw_spawn_registers_capabilities(self):
        from amplifier_app_openclaw.spawn import OpenClawSpawnManager
        mock_prepared = MagicMock()
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        sm = OpenClawSpawnManager(mock_prepared, "p1", writer)

        mock_coord = MagicMock()
        sm.register(mock_coord)
        assert mock_coord.register_capability.call_count == 2
        calls = [c[0][0] for c in mock_coord.register_capability.call_args_list]
        assert "session.spawn" in calls
        assert "session.resume" in calls
