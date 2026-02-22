"""Tests for the tool-openclaw Amplifier module."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_app_openclaw.modules.tool_openclaw import mount, __amplifier_module_type__
from amplifier_app_openclaw.rpc import JsonRpcResponseReader


def _make_coordinator(rpc_reader=None):
    """Create a mock coordinator with capability support."""
    capabilities = {}
    if rpc_reader is not None:
        capabilities["openclaw.rpc_reader"] = rpc_reader

    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(side_effect=lambda name: capabilities.get(name))
    coordinator.mount = AsyncMock()
    return coordinator


class TestModuleMetadata:
    def test_module_type(self):
        assert __amplifier_module_type__ == "tool"


class TestMountDefaultConfig:
    @pytest.mark.asyncio
    async def test_mounts_all_five_tools(self):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        coord = _make_coordinator(rpc)

        await mount(coord)

        assert coord.mount.call_count == 5
        mounted_names = {call.kwargs["name"] for call in coord.mount.call_args_list}
        assert mounted_names == {
            "openclaw_message",
            "openclaw_browser",
            "openclaw_memory",
            "openclaw_devices",
            "openclaw_cron",
        }


class TestMountWithWhitelist:
    @pytest.mark.asyncio
    async def test_partial_whitelist(self):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        coord = _make_coordinator(rpc)

        await mount(coord, config={"tools": ["message", "cron"]})

        assert coord.mount.call_count == 2
        mounted_names = {call.kwargs["name"] for call in coord.mount.call_args_list}
        assert mounted_names == {"openclaw_message", "openclaw_cron"}

    @pytest.mark.asyncio
    async def test_empty_whitelist(self):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        coord = _make_coordinator(rpc)

        await mount(coord, config={"tools": []})

        assert coord.mount.call_count == 0

    @pytest.mark.asyncio
    async def test_single_tool(self):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        coord = _make_coordinator(rpc)

        await mount(coord, config={"tools": ["browser"]})

        assert coord.mount.call_count == 1
        assert coord.mount.call_args_list[0].kwargs["name"] == "openclaw_browser"


class TestMountMissingCapability:
    @pytest.mark.asyncio
    async def test_raises_without_rpc_reader(self):
        coord = _make_coordinator(rpc_reader=None)

        with pytest.raises(RuntimeError, match="openclaw.rpc_reader"):
            await mount(coord)


class TestMountedToolProperties:
    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self):
        rpc = MagicMock(spec=JsonRpcResponseReader)
        coord = _make_coordinator(rpc)

        await mount(coord)

        for call in coord.mount.call_args_list:
            tool = call.args[1]  # positional: ("tools", tool_instance)
            assert len(tool.description) > 10
            assert tool.input_schema["type"] == "object"
