"""Amplifier module that mounts OpenClaw tool bridges."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount OpenClaw tools as Amplifier tools.

    Reads the ``openclaw.rpc_reader`` capability from the coordinator to
    construct tool instances.  An optional *config* dict may contain a
    ``tools`` key with a list of tool short-names to enable (whitelist).
    When omitted, all five tools are mounted.
    """
    config = config or {}

    # Get RPC reader from coordinator capability
    rpc_reader = coordinator.get_capability("openclaw.rpc_reader")
    if rpc_reader is None:
        raise RuntimeError("openclaw.rpc_reader capability not registered")

    from amplifier_app_openclaw.tools import (
        OpenClawBrowserTool,
        OpenClawCronTool,
        OpenClawDevicesTool,
        OpenClawMemoryTool,
        OpenClawMessageTool,
    )

    ALL_TOOLS = ["message", "browser", "memory", "devices", "cron"]
    enabled = config.get("tools", ALL_TOOLS)

    tool_classes = {
        "message": OpenClawMessageTool,
        "browser": OpenClawBrowserTool,
        "memory": OpenClawMemoryTool,
        "devices": OpenClawDevicesTool,
        "cron": OpenClawCronTool,
    }

    for name in enabled:
        cls = tool_classes.get(name)
        if cls is None:
            logger.warning("Unknown OpenClaw tool: %s — skipping", name)
            continue
        tool = cls(rpc_reader)
        await coordinator.mount("tools", tool, name=tool.name)

    logger.info("Mounted %d OpenClaw tool(s): %s", len(enabled), enabled)
