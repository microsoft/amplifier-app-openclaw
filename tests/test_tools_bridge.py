"""Tests for OpenClaw tool bridges (tools/)."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_app_openclaw.rpc import JsonRpcError, JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.tools.browser import OpenClawBrowserTool
from amplifier_app_openclaw.tools.cron import OpenClawCronTool
from amplifier_app_openclaw.tools.devices import OpenClawDevicesTool
from amplifier_app_openclaw.tools.memory import OpenClawMemoryTool
from amplifier_app_openclaw.tools.message import OpenClawMessageTool
from amplifier_app_openclaw.tools import create_openclaw_tools


ALL_TOOLS = [
    (OpenClawMessageTool, "openclaw_message"),
    (OpenClawBrowserTool, "openclaw_browser"),
    (OpenClawMemoryTool, "openclaw_memory"),
    (OpenClawDevicesTool, "openclaw_devices"),
    (OpenClawCronTool, "openclaw_cron"),
]


class TestToolProperties:
    """Each tool has name, description, input_schema."""

    @pytest.mark.parametrize("cls,expected_name", ALL_TOOLS)
    def test_name(self, cls, expected_name):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        tool = cls(rpc)
        assert tool.name == expected_name

    @pytest.mark.parametrize("cls,_", ALL_TOOLS)
    def test_description_nonempty(self, cls, _):
        tool = cls(MagicMock(spec=JsonRpcResponseReader))
        assert len(tool.description) > 10

    @pytest.mark.parametrize("cls,_", ALL_TOOLS)
    def test_schema_is_object(self, cls, _):
        tool = cls(MagicMock(spec=JsonRpcResponseReader))
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "properties" in schema


class TestToolExecute:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls,name", ALL_TOOLS)
    async def test_success_response(self, cls, name):
        rpc = AsyncMock(spec=JsonRpcResponseReader)
        rpc.request = AsyncMock(return_value={"data": "ok"})
        tool = cls(rpc)

        result = await tool.execute({"action": "test"})
        assert result.success is True
        assert result.output == {"data": "ok"}

        rpc.request.assert_called_once_with(
            "openclaw/tool_call",
            {"tool": name, "input": {"action": "test"}},
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls,name", ALL_TOOLS)
    async def test_rpc_error_response(self, cls, name):
        rpc = AsyncMock(spec=JsonRpcResponseReader)
        rpc.request = AsyncMock(side_effect=JsonRpcError({"code": -1, "message": "fail"}))
        tool = cls(rpc)

        result = await tool.execute({"action": "test"})
        assert result.success is False
        assert result.error["code"] == -1

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        rpc = AsyncMock(spec=JsonRpcResponseReader)
        rpc.request = AsyncMock(side_effect=RuntimeError("connection lost"))
        tool = OpenClawMessageTool(rpc)

        result = await tool.execute({"action": "send", "message": "hi"})
        assert result.success is False
        assert "connection lost" in result.error["message"]


class TestCreateOpenclawTools:
    def test_creates_five_tools(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rpc = MagicMock(spec=JsonRpcResponseReader)
        tools = create_openclaw_tools(writer, rpc)
        assert len(tools) == 5
        names = {t.name for t in tools}
        assert names == {"openclaw_message", "openclaw_browser", "openclaw_memory", "openclaw_devices", "openclaw_cron"}
