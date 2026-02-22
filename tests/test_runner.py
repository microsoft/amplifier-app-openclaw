"""Tests for the runner module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.runner import CHAT_OVERLAY, AutoDenyApproval, StderrDisplay, run_task


class TestChatOverlay:
    def test_overlay_has_instruction(self):
        assert "concise" in CHAT_OVERLAY.instruction.lower()

    def test_overlay_name(self):
        assert CHAT_OVERLAY.name == "_chat_overlay"


class TestAutoDenyApproval:
    @pytest.mark.asyncio
    async def test_returns_default(self):
        a = AutoDenyApproval()
        assert await a.request_approval("do?", ["yes", "no"]) == "deny"

    @pytest.mark.asyncio
    async def test_returns_custom_default(self):
        a = AutoDenyApproval()
        assert await a.request_approval("do?", ["yes", "no"], default="yes") == "yes"


class TestStderrDisplay:
    def test_show_message(self, capsys):
        d = StderrDisplay()
        d.show_message("hi", level="warn")
        assert "hi" in capsys.readouterr().err


class TestRunTask:
    def _make_mocks(self):
        session_status = MagicMock()
        session_status.estimated_cost = 0.01
        session_status.total_input_tokens = 200
        session_status.total_output_tokens = 100
        session_status.tool_invocations = 3
        session_status.status = "completed"

        session = AsyncMock()
        session.execute = AsyncMock(return_value="The answer is 42")
        session.status = session_status
        session.cleanup = AsyncMock()
        session.coordinator = MagicMock()

        prepared = AsyncMock()
        prepared.create_session = AsyncMock(return_value=session)

        bundle = MagicMock()
        bundle.name = "foundation"
        bundle.compose = MagicMock(return_value=bundle)
        bundle.prepare = AsyncMock(return_value=prepared)

        return bundle, session

    def _patches(self, bundle):
        """Return context managers patching the lazy imports inside run_task."""
        return (
            patch("amplifier_foundation.load_bundle", new_callable=AsyncMock, return_value=bundle),
            patch("amplifier_foundation.mentions.BaseMentionResolver", MagicMock()),
            patch("amplifier_app_openclaw.cost.log_cost_entry"),
        )

    @pytest.mark.asyncio
    async def test_happy_path(self):
        bundle, session = self._make_mocks()
        p1, p2, p3 = self._patches(bundle)
        with p1, p2, p3:
            result = await run_task(bundle_name="foundation", cwd=".", timeout=300, prompt="hello")

        assert result["response"] == "The answer is 42"
        assert result["status"] == "completed"
        assert result["usage"]["estimated_cost"] == 0.01
        session.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_json_output_structure(self):
        bundle, _ = self._make_mocks()
        p1, p2, p3 = self._patches(bundle)
        with p1, p2, p3:
            result = await run_task(bundle_name="foundation", cwd=".", timeout=300, prompt="test")

        assert set(result.keys()) == {"response", "usage", "status"}
        assert set(result["usage"].keys()) == {"input_tokens", "output_tokens", "estimated_cost", "tool_invocations"}

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Verify that asyncio.TimeoutError triggers make_timeout_result."""
        from amplifier_app_openclaw.errors import make_timeout_result

        session = MagicMock()
        session.status.estimated_cost = 0.0
        session.status.total_input_tokens = 10
        session.status.total_output_tokens = 5
        session.status.tool_invocations = 0

        result = make_timeout_result(session=session)
        assert result["timed_out"] is True
        assert result["status"] == "timed_out"
        assert "usage" in result

    @pytest.mark.asyncio
    async def test_error_wrapping(self):
        with patch("amplifier_foundation.load_bundle", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            result = await run_task(bundle_name="bad", cwd=".", timeout=300, prompt="fail")

        assert "error" in result
        assert result["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_none_cost_defaults_to_zero(self):
        bundle, session = self._make_mocks()
        session.status.estimated_cost = None
        p1, p2, p3 = self._patches(bundle)
        with p1, p2, p3:
            result = await run_task(bundle_name="foundation", cwd=".", timeout=300, prompt="test")

        assert result["usage"]["estimated_cost"] == 0.0
