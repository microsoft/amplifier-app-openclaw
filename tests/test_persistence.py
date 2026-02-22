"""Tests for session persistence (Phase 1.5.2)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.rpc import JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.session_manager import (
    SessionManager,
    _deterministic_session_id,
    _PERSISTENT_SESSIONS_DIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(max_bundles=2, max_sessions=4):
    buf = io.StringIO()
    writer = JsonRpcWriter(buf)
    rr = MagicMock(spec=JsonRpcResponseReader)
    with patch.dict("os.environ", {
        "AMPLIFIER_MAX_BUNDLES": str(max_bundles),
        "AMPLIFIER_MAX_SESSIONS": str(max_sessions),
    }):
        mgr = SessionManager(writer, rr)
    return mgr


def _mock_prepared(mount_plan=None):
    """Create a mock PreparedBundle."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value="response text")
    mock_session.cleanup = AsyncMock()
    mock_session.coordinator.hooks = MagicMock()
    mock_session.coordinator.hooks.register.return_value = lambda: None
    mock_session.coordinator.register_capability = MagicMock()
    mock_session.coordinator.mount = AsyncMock()
    mock_session.coordinator.mount_points = {"tools": {}}
    mock_session.coordinator.cancellation = MagicMock()
    mock_session.config = {"agents": {"default": {}}}
    mock_session.initialize = AsyncMock()

    status = MagicMock()
    status.status = "completed"
    status.total_input_tokens = 100
    status.total_output_tokens = 50
    status.estimated_cost = 0.01
    status.tool_invocations = 1
    mock_session.status = status

    prepared = MagicMock()
    prepared.create_session = AsyncMock(return_value=mock_session)
    prepared.mount_plan = mount_plan or {"session": {"context": {"module": "context-simple"}}}
    prepared.resolver = MagicMock()
    prepared.bundle_package_paths = []
    prepared.bundle = MagicMock()
    return prepared, mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeterministicSessionId:
    def test_same_inputs_same_id(self):
        id1 = _deterministic_session_id("my-bundle", "research-session")
        id2 = _deterministic_session_id("my-bundle", "research-session")
        assert id1 == id2

    def test_different_names_different_ids(self):
        id1 = _deterministic_session_id("my-bundle", "session-a")
        id2 = _deterministic_session_id("my-bundle", "session-b")
        assert id1 != id2

    def test_different_bundles_different_ids(self):
        id1 = _deterministic_session_id("bundle-a", "session")
        id2 = _deterministic_session_id("bundle-b", "session")
        assert id1 != id2

    def test_id_length(self):
        sid = _deterministic_session_id("bundle", "name")
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)


class TestPersistentSessionCreate:
    @pytest.mark.asyncio
    async def test_non_persistent_uses_standard_path(self):
        """Non-persistent sessions use prepared.create_session normally."""
        mgr = _make_manager()
        prepared, mock_session = _mock_prepared()

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)

                result = await mgr.handle_create({"bundle": "test-bundle"})

        assert result["persistent"] is False
        assert result["resumed"] is False
        prepared.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_persistent_deepcopies_mount_plan(self):
        """Persistent sessions deep-copy mount_plan, don't mutate cached bundle."""
        mgr = _make_manager()
        original_plan = {"session": {"context": {"module": "context-simple"}}}
        prepared, mock_session = _mock_prepared(mount_plan=original_plan)

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)
                with patch("amplifier_app_openclaw.session_manager._context_persistent_available", return_value=True):
                    mock_amp_session = MagicMock()
                    mock_amp_session.coordinator.mount = AsyncMock()
                    mock_amp_session.coordinator.hooks = MagicMock()
                    mock_amp_session.coordinator.hooks.register.return_value = lambda: None
                    mock_amp_session.coordinator.register_capability = MagicMock()
                    mock_amp_session.coordinator.mount_points = {"tools": {}}
                    mock_amp_session.config = {"agents": {"default": {}}}
                    mock_amp_session.initialize = AsyncMock()
                    mock_amp_session.status = MagicMock()

                    with patch("amplifier_core.AmplifierSession", return_value=mock_amp_session) as mock_cls:
                        result = await mgr.handle_create({
                            "bundle": "test-bundle",
                            "persistent": True,
                        })

        assert result["persistent"] is True
        # Original mount_plan should be unchanged
        assert original_plan["session"]["context"]["module"] == "context-simple"
        # AmplifierSession should have been called with modified plan
        call_args = mock_cls.call_args
        used_plan = call_args[0][0]
        assert used_plan["session"]["context"]["module"] == "context-persistent"

    @pytest.mark.asyncio
    async def test_persistent_fallback_when_module_unavailable(self):
        """Falls back to non-persistent if context-persistent is not installed."""
        mgr = _make_manager()
        prepared, _ = _mock_prepared()

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)
                with patch("amplifier_app_openclaw.session_manager._context_persistent_available", return_value=False):
                    result = await mgr.handle_create({
                        "bundle": "test-bundle",
                        "persistent": True,
                    })

        assert result["persistent"] is False
        prepared.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_name_generates_deterministic_id(self):
        """session_name produces a deterministic session_id."""
        mgr = _make_manager()
        prepared, _ = _mock_prepared()

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)
                with patch("amplifier_app_openclaw.session_manager._context_persistent_available", return_value=False):
                    r1 = await mgr.handle_create({
                        "bundle": "test-bundle",
                        "session_name": "my-research",
                        "persistent": True,
                    })

        expected_id = _deterministic_session_id("test-bundle", "my-research")
        assert r1["session_id"] == expected_id


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_resume_nonexistent_raises(self):
        """Resuming a session with no saved data raises ValueError."""
        mgr = _make_manager()
        with pytest.raises(ValueError, match="No saved session found"):
            await mgr.handle_resume({
                "session_id": "nonexistent-id",
                "bundle": "test-bundle",
            })

    @pytest.mark.asyncio
    async def test_resume_with_existing_storage(self):
        """Resuming a session with existing storage calls handle_create with _is_resumed."""
        mgr = _make_manager()
        session_id = "test-resume-id"

        # Create fake storage
        session_dir = _PERSISTENT_SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "context-messages.jsonl").write_text('{"role":"user","content":"hello"}\n')

        try:
            # Patch handle_create to capture the params it receives
            original_create = mgr.handle_create
            create_params = {}

            async def capture_create(params):
                create_params.update(params)
                return {"session_id": session_id, "agents": [], "tools": [], "persistent": True, "resumed": True}

            mgr.handle_create = capture_create

            result = await mgr.handle_resume({
                "session_id": session_id,
                "bundle": "test-bundle",
            })
            assert result["resumed"] is True
            assert create_params["_is_resumed"] is True
            assert create_params["persistent"] is True
            assert create_params["session_id"] == session_id
        finally:
            (session_dir / "context-messages.jsonl").unlink(missing_ok=True)
            session_dir.rmdir()

    @pytest.mark.asyncio
    async def test_resume_missing_params(self):
        mgr = _make_manager()
        with pytest.raises(ValueError, match="session_id"):
            await mgr.handle_resume({"bundle": "test"})
        with pytest.raises(ValueError, match="bundle"):
            await mgr.handle_resume({"session_id": "abc"})


class TestSessionListMetadata:
    @pytest.mark.asyncio
    async def test_list_includes_persistence_metadata(self):
        """Session list includes persistent and session_name metadata."""
        mgr = _make_manager()
        prepared, _ = _mock_prepared()

        with patch("amplifier_app_openclaw.session_manager.load_bundle", new=AsyncMock(return_value=MagicMock())):
            with patch("amplifier_app_openclaw.session_manager.CHAT_OVERLAY") as overlay:
                overlay.compose.return_value = MagicMock()
                overlay.compose.return_value.prepare = AsyncMock(return_value=prepared)
                with patch("amplifier_app_openclaw.session_manager._context_persistent_available", return_value=False):
                    await mgr.handle_create({
                        "bundle": "test",
                        "session_name": "research",
                        "persistent": True,
                    })

        result = await mgr.handle_list({})
        assert len(result["sessions"]) == 1
        session_meta = result["sessions"][0]
        assert session_meta["session_id"] == _deterministic_session_id("test", "research")
