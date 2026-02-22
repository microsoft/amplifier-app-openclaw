"""Tests for session manager (session_manager.py)."""

from __future__ import annotations

import asyncio
import io
import json
import time
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.rpc import JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.session_manager import SessionManager, SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(max_bundles=2, max_sessions=2):
    buf = io.StringIO()
    writer = JsonRpcWriter(buf)
    rr = MagicMock(spec=JsonRpcResponseReader)
    with patch.dict("os.environ", {
        "AMPLIFIER_MAX_BUNDLES": str(max_bundles),
        "AMPLIFIER_MAX_SESSIONS": str(max_sessions),
    }):
        mgr = SessionManager(writer, rr)
    return mgr, buf


def _mock_prepared():
    """Create a mock PreparedBundle that returns a usable mock session."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value="response text")
    mock_session.cleanup = AsyncMock()
    mock_session.coordinator.hooks = MagicMock()
    mock_session.coordinator.hooks.register.return_value = lambda: None
    mock_session.coordinator.register_capability = MagicMock()
    mock_session.coordinator.mount_points = {"tools": {}}
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.coordinator.cancellation.request_graceful = AsyncMock()
    mock_session.coordinator.cancellation.request_immediate = AsyncMock()
    mock_session.config = {"agents": {"default": {}}}

    status = MagicMock()
    status.status = "completed"
    status.total_input_tokens = 100
    status.total_output_tokens = 50
    status.estimated_cost = 0.01
    status.tool_invocations = 1
    mock_session.status = status

    prepared = MagicMock()
    prepared.create_session = AsyncMock(return_value=mock_session)
    prepared.bundle = MagicMock()
    return prepared


# ---------------------------------------------------------------------------
# Bundle Cache Tests
# ---------------------------------------------------------------------------

class TestBundleCache:
    @pytest.mark.asyncio
    async def test_cache_stores_bundle(self):
        mgr, _ = _make_manager(max_bundles=2)
        prepared = _mock_prepared()

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)

                await mgr._get_or_prepare_bundle("bundle-a")
                assert "bundle-a" in mgr._bundle_cache
                assert len(mgr._bundle_cache) == 1

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        mgr, _ = _make_manager(max_bundles=2)
        mgr._bundle_cache["cached"] = "prepared_obj"
        result = await mgr._get_or_prepare_bundle("cached")
        assert result == "prepared_obj"

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        mgr, _ = _make_manager(max_bundles=2)
        mgr._bundle_cache["old"] = "old_prepared"
        mgr._bundle_cache["newer"] = "newer_prepared"

        mock_bundle = MagicMock()
        mock_bundle.prepare = AsyncMock(return_value="newest_prepared")

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                with patch("amplifier_app_openclaw.runner._inject_user_providers"):
                    overlay.compose.return_value = mock_bundle

                    await mgr._get_or_prepare_bundle("newest")

        # 'old' should be evicted (LRU)
        assert "old" not in mgr._bundle_cache
        assert "newer" in mgr._bundle_cache
        assert "newest" in mgr._bundle_cache

    @pytest.mark.asyncio
    async def test_lru_touch_on_access(self):
        mgr, _ = _make_manager(max_bundles=2)
        mgr._bundle_cache["a"] = "a_prep"
        mgr._bundle_cache["b"] = "b_prep"

        # Access 'a' to make it most recently used
        with patch("amplifier_app_openclaw.runner._inject_user_providers"):
            await mgr._get_or_prepare_bundle("a")

        mock_bundle = MagicMock()
        mock_bundle.prepare = AsyncMock(return_value="c_prep")

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                with patch("amplifier_app_openclaw.runner._inject_user_providers"):
                    overlay.compose.return_value = mock_bundle
                    await mgr._get_or_prepare_bundle("c")

        # 'b' should be evicted since 'a' was touched
        assert "a" in mgr._bundle_cache
        assert "b" not in mgr._bundle_cache
        assert "c" in mgr._bundle_cache


# ---------------------------------------------------------------------------
# Session Lifecycle Tests
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_create_session(self):
        mgr, _ = _make_manager()
        prepared = _mock_prepared()
        mgr._bundle_cache["test-bundle"] = prepared

        with patch("amplifier_app_openclaw.session_manager.create_openclaw_tools", return_value=[]):
            result = await mgr.handle_create({"bundle": "test-bundle", "cwd": "/tmp"})

        assert "session_id" in result
        assert result["session_id"] in mgr._sessions

    @pytest.mark.asyncio
    async def test_create_missing_bundle_raises(self):
        mgr, _ = _make_manager()
        with pytest.raises(ValueError, match="Missing required param"):
            await mgr.handle_create({})

    @pytest.mark.asyncio
    async def test_max_sessions_enforced(self):
        mgr, _ = _make_manager(max_sessions=1)
        prepared = _mock_prepared()
        mgr._bundle_cache["b"] = prepared

        with patch("amplifier_app_openclaw.session_manager.create_openclaw_tools", return_value=[]):
            await mgr.handle_create({"bundle": "b"})

        with pytest.raises(RuntimeError, match="Max concurrent sessions"):
            with patch("amplifier_app_openclaw.session_manager.create_openclaw_tools", return_value=[]):
                await mgr.handle_create({"bundle": "b"})

    @pytest.mark.asyncio
    async def test_execute_session(self):
        mgr, _ = _make_manager()
        prepared = _mock_prepared()
        mgr._bundle_cache["b"] = prepared

        with patch("amplifier_app_openclaw.session_manager.create_openclaw_tools", return_value=[]):
            create_result = await mgr.handle_create({"bundle": "b"})

        sid = create_result["session_id"]

        with patch("amplifier_app_openclaw.session_manager.log_cost_entry"):
            result = await mgr.handle_execute({"session_id": sid, "prompt": "hello"})

        assert result["response"] == "response text"
        assert "usage" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_session_raises(self):
        mgr, _ = _make_manager()
        with pytest.raises(ValueError, match="Unknown session"):
            await mgr.handle_execute({"session_id": "nope", "prompt": "hi"})

    @pytest.mark.asyncio
    async def test_execute_missing_prompt_raises(self):
        mgr, _ = _make_manager()
        mgr._sessions["s1"] = MagicMock()
        with pytest.raises(ValueError, match="Missing required param: prompt"):
            await mgr.handle_execute({"session_id": "s1"})

    @pytest.mark.asyncio
    async def test_cancel_session(self):
        mgr, _ = _make_manager()
        mock_state = MagicMock()
        mock_state.session.coordinator.cancellation.request_graceful = AsyncMock()
        mock_state.session.coordinator.cancellation.request_immediate = AsyncMock()
        mock_state.metadata = {"status": "executing"}
        mgr._sessions["s1"] = mock_state

        result = await mgr.handle_cancel({"session_id": "s1"})
        assert result["status"] == "cancelling"
        mock_state.session.coordinator.cancellation.request_graceful.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_immediate(self):
        mgr, _ = _make_manager()
        mock_state = MagicMock()
        mock_state.session.coordinator.cancellation.request_immediate = AsyncMock()
        mock_state.metadata = {"status": "executing"}
        mgr._sessions["s1"] = mock_state

        await mgr.handle_cancel({"session_id": "s1", "immediate": True})
        mock_state.session.coordinator.cancellation.request_immediate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_session(self):
        mgr, _ = _make_manager()
        mock_state = MagicMock()
        mock_state.session.cleanup = AsyncMock()
        mock_state.streaming_hook.unregister = MagicMock()
        mgr._sessions["s1"] = mock_state

        result = await mgr.handle_cleanup({"session_id": "s1"})
        assert result["status"] == "cleaned_up"
        assert "s1" not in mgr._sessions
        mock_state.streaming_hook.unregister.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_unknown_raises(self):
        mgr, _ = _make_manager()
        with pytest.raises(ValueError, match="Unknown session"):
            await mgr.handle_cleanup({"session_id": "nope"})

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        mgr, _ = _make_manager()
        mock_state = MagicMock()
        mock_state.metadata = {"bundle": "b1", "status": "ready", "created_at": 123}
        mgr._sessions["s1"] = mock_state

        result = await mgr.handle_list({})
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == "s1"


# ---------------------------------------------------------------------------
# Approval Response Routing
# ---------------------------------------------------------------------------

class TestApprovalRouting:
    @pytest.mark.asyncio
    async def test_approval_response_routed(self):
        mgr, _ = _make_manager()
        mock_approval = MagicMock()
        mock_state = MagicMock()
        mock_state.approval_system = mock_approval
        mgr._sessions["s1"] = mock_state

        result = await mgr.handle_approval_response({
            "session_id": "s1",
            "request_id": "r1",
            "selected_option": "allow",
        })
        assert result["status"] == "ok"
        mock_approval.resolve_approval.assert_called_once_with("r1", "allow")

    @pytest.mark.asyncio
    async def test_approval_response_unknown_session(self):
        mgr, _ = _make_manager()
        with pytest.raises(ValueError, match="Unknown session"):
            await mgr.handle_approval_response({
                "session_id": "nope",
                "request_id": "r1",
                "selected_option": "deny",
            })


# ---------------------------------------------------------------------------
# Handler Registration
# ---------------------------------------------------------------------------

class TestHandlerRegistration:
    def test_register_handlers(self):
        mgr, _ = _make_manager()
        mock_reader = MagicMock()
        mgr.register_handlers(mock_reader)
        registered = {call[0][0] for call in mock_reader.register.call_args_list}
        expected = {
            "session/create", "session/execute", "session/cancel",
            "session/cleanup", "session/list", "session/approval_response",
            "session/inject", "session/resume",
            "bundle/list", "bundle/add",
            "augment/evaluate_tool", "augment/cost_report", "augment/query_context",
            "augment/list_tools", "augment/discover",
        }
        assert expected == registered
