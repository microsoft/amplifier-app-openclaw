"""Tests for mid-execution message injection (Phase 1.5.1)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.injection import InjectionManager


# -- InjectionManager unit tests ------------------------------------------------


@pytest.mark.asyncio
async def test_inject_enqueues_message():
    mgr = InjectionManager()
    await mgr.inject("hello")
    assert mgr._queue.qsize() == 1


@pytest.mark.asyncio
async def test_hook_returns_continue_when_empty():
    mgr = InjectionManager()
    result = await mgr.hook_handler("provider:request", {})
    assert result.action == "continue"
    assert result.data == {}


@pytest.mark.asyncio
async def test_hook_returns_inject_context_when_queued():
    mgr = InjectionManager()
    await mgr.inject("fix the bug")
    result = await mgr.hook_handler("provider:request", {"iteration": 1})
    assert result.action == "inject_context"
    assert "[User interjection]: fix the bug" in result.context_injection
    assert result.context_injection_role == "user"
    assert result.ephemeral is True


@pytest.mark.asyncio
async def test_hook_drains_multiple_messages():
    mgr = InjectionManager()
    await mgr.inject("msg1")
    await mgr.inject("msg2")
    result = await mgr.hook_handler("provider:request", {})
    assert result.action == "inject_context"
    assert "[User interjection]: msg1" in result.context_injection
    assert "[User interjection]: msg2" in result.context_injection
    # Queue should be empty after drain
    assert mgr._queue.empty()


@pytest.mark.asyncio
async def test_hook_empty_after_drain():
    mgr = InjectionManager()
    await mgr.inject("once")
    await mgr.hook_handler("provider:request", {})
    # Second call should return continue
    result = await mgr.hook_handler("provider:request", {})
    assert result.action == "continue"


# -- handle_inject integration tests -------------------------------------------


@pytest.mark.asyncio
async def test_handle_inject_unknown_session():
    from amplifier_app_openclaw.session_manager import SessionManager

    writer = MagicMock()
    response_reader = MagicMock()
    sm = SessionManager(writer, response_reader)

    with pytest.raises(ValueError, match="Unknown session"):
        await sm.handle_inject({"session_id": "nonexistent", "message": "hi"})


@pytest.mark.asyncio
async def test_handle_inject_not_executing():
    from amplifier_app_openclaw.session_manager import SessionManager, SessionState

    writer = MagicMock()
    response_reader = MagicMock()
    sm = SessionManager(writer, response_reader)

    mgr = InjectionManager()
    state = SessionState(
        session=MagicMock(),
        approval_system=MagicMock(),
        streaming_hook=MagicMock(),
        spawn_manager=MagicMock(),
        display_system=MagicMock(),
        injection_manager=mgr,
        metadata={"status": "ready"},
    )
    sm._sessions["test-session"] = state

    with pytest.raises(RuntimeError, match="not currently executing"):
        await sm.handle_inject({"session_id": "test-session", "message": "hi"})


@pytest.mark.asyncio
async def test_handle_inject_success():
    from amplifier_app_openclaw.session_manager import SessionManager, SessionState

    writer = MagicMock()
    response_reader = MagicMock()
    sm = SessionManager(writer, response_reader)

    mgr = InjectionManager()
    state = SessionState(
        session=MagicMock(),
        approval_system=MagicMock(),
        streaming_hook=MagicMock(),
        spawn_manager=MagicMock(),
        display_system=MagicMock(),
        injection_manager=mgr,
        metadata={"status": "executing"},
    )
    sm._sessions["test-session"] = state

    result = await sm.handle_inject({"session_id": "test-session", "message": "stop that"})
    assert result["status"] == "injected"
    assert mgr._queue.qsize() == 1


@pytest.mark.asyncio
async def test_handle_inject_missing_message():
    from amplifier_app_openclaw.session_manager import SessionManager

    writer = MagicMock()
    response_reader = MagicMock()
    sm = SessionManager(writer, response_reader)

    with pytest.raises(ValueError, match="Missing required param: message"):
        await sm.handle_inject({"session_id": "x", "message": ""})
