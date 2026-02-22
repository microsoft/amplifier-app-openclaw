"""Tests for bidirectional capability discovery (discovery.py + session_manager handlers)."""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.discovery import (
    ToolSpec,
    discover_openclaw_tools,
    list_session_tools,
    register_amplifier_tools,
)
from amplifier_app_openclaw.rpc import JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.session_manager import SessionManager


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------


def test_toolspec_creation():
    ts = ToolSpec(name="foo", description="does foo", input_schema={"type": "object"})
    assert ts.name == "foo"
    assert ts.description == "does foo"
    assert ts.input_schema == {"type": "object"}


# ---------------------------------------------------------------------------
# discover_openclaw_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_openclaw_tools_success():
    rpc = AsyncMock(spec=JsonRpcResponseReader)
    rpc.request.return_value = {
        "tools": [
            {"name": "browser", "description": "Browse", "input_schema": {"type": "object"}},
            {"name": "memory", "description": "Memory", "input_schema": {}},
        ]
    }
    result = await discover_openclaw_tools(rpc)
    assert len(result) == 2
    assert result[0].name == "browser"
    assert isinstance(result[1], ToolSpec)


@pytest.mark.asyncio
async def test_discover_openclaw_tools_graceful_failure():
    rpc = AsyncMock(spec=JsonRpcResponseReader)
    rpc.request.side_effect = Exception("not supported")
    result = await discover_openclaw_tools(rpc)
    assert result == []


# ---------------------------------------------------------------------------
# list_session_tools
# ---------------------------------------------------------------------------


def test_list_session_tools():
    tool1 = MagicMock()
    tool1.name = "browser"
    tool1.description = "Browse the web"
    tool1.input_schema = {"type": "object", "properties": {"url": {"type": "string"}}}

    tool2 = MagicMock()
    tool2.name = "memory"
    tool2.description = "Read/write memory"
    tool2.input_schema = {}

    session = MagicMock()
    session.coordinator.mount_points = {"tools": {"browser": tool1, "memory": tool2}}

    result = list_session_tools(session)
    assert len(result) == 2
    assert result[0]["name"] == "browser"
    assert result[1]["name"] == "memory"
    assert "input_schema" in result[0]


def test_list_session_tools_empty():
    session = MagicMock()
    session.coordinator.mount_points = {}
    assert list_session_tools(session) == []


# ---------------------------------------------------------------------------
# register_amplifier_tools
# ---------------------------------------------------------------------------


def test_register_amplifier_tools():
    buf = io.StringIO()
    writer = JsonRpcWriter(buf)
    tools = [{"name": "browser", "description": "Browse", "input_schema": {}}]

    register_amplifier_tools(writer, "sess-123", tools)

    line = buf.getvalue().strip()
    msg = json.loads(line)
    assert msg["method"] == "amplifier/tools_available"
    assert msg["params"]["session_id"] == "sess-123"
    assert msg["params"]["tools"] == tools
    assert "id" not in msg  # notification, no id


# ---------------------------------------------------------------------------
# Session manager handlers
# ---------------------------------------------------------------------------


def _make_manager():
    buf = io.StringIO()
    writer = JsonRpcWriter(buf)
    rr = MagicMock(spec=JsonRpcResponseReader)
    with patch.dict("os.environ", {
        "AMPLIFIER_MAX_BUNDLES": "2",
        "AMPLIFIER_MAX_SESSIONS": "2",
    }):
        mgr = SessionManager(writer, rr)
    return mgr, buf, rr


def _make_session_state(tools=None):
    from amplifier_app_openclaw.session_manager import SessionState

    tool_map = {}
    for t in (tools or []):
        mock_tool = MagicMock()
        mock_tool.name = t["name"]
        mock_tool.description = t["description"]
        mock_tool.input_schema = t["input_schema"]
        tool_map[t["name"]] = mock_tool

    session = MagicMock()
    session.coordinator.mount_points = {"tools": tool_map}

    return SessionState(
        session=session,
        approval_system=MagicMock(),
        streaming_hook=MagicMock(),
        spawn_manager=MagicMock(),
        display_system=MagicMock(),
    )


@pytest.mark.asyncio
async def test_handle_list_tools():
    mgr, buf, rr = _make_manager()
    state = _make_session_state([
        {"name": "browser", "description": "Browse", "input_schema": {"type": "object"}},
    ])
    mgr._sessions["s1"] = state

    result = await mgr.handle_list_tools({"session_id": "s1"})
    assert len(result["tools"]) == 1
    assert result["tools"][0]["name"] == "browser"


@pytest.mark.asyncio
async def test_handle_list_tools_unknown_session():
    mgr, _, _ = _make_manager()
    with pytest.raises(ValueError, match="Unknown session"):
        await mgr.handle_list_tools({"session_id": "nope"})


@pytest.mark.asyncio
async def test_handle_discover_with_session():
    mgr, buf, rr = _make_manager()
    state = _make_session_state([
        {"name": "memory", "description": "Mem", "input_schema": {}},
    ])
    mgr._sessions["s1"] = state

    # OpenClaw tools_list will fail (not supported yet)
    rr.request = AsyncMock(side_effect=Exception("nope"))

    result = await mgr.handle_discover({"session_id": "s1"})
    assert len(result["amplifier_tools"]) == 1
    assert result["openclaw_tools"] == []


@pytest.mark.asyncio
async def test_handle_discover_no_session():
    mgr, _, rr = _make_manager()
    rr.request = AsyncMock(side_effect=Exception("nope"))

    result = await mgr.handle_discover({})
    assert result["amplifier_tools"] == []
    assert result["openclaw_tools"] == []
