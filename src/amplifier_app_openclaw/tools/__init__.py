"""OpenClaw tool bridges for Amplifier agents."""

from __future__ import annotations

from amplifier_app_openclaw.rpc import JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.tools.base import OpenClawToolBase
from amplifier_app_openclaw.tools.browser import OpenClawBrowserTool
from amplifier_app_openclaw.tools.cron import OpenClawCronTool
from amplifier_app_openclaw.tools.devices import OpenClawDevicesTool
from amplifier_app_openclaw.tools.memory import OpenClawMemoryTool
from amplifier_app_openclaw.tools.message import OpenClawMessageTool

__all__ = [
    "OpenClawToolBase",
    "OpenClawBrowserTool",
    "OpenClawCronTool",
    "OpenClawDevicesTool",
    "OpenClawMemoryTool",
    "OpenClawMessageTool",
    "create_openclaw_tools",
]


def create_openclaw_tools(
    writer: JsonRpcWriter,
    reader: JsonRpcResponseReader,
) -> list[OpenClawToolBase]:
    """Create all 5 OpenClaw tool bridges.

    Args:
        writer: JSON-RPC writer (unused directly; reader wraps it).
        reader: JSON-RPC response reader for sending requests and awaiting responses.

    Returns:
        List of 5 tool instances ready to mount on an Amplifier coordinator.
    """
    return [
        OpenClawMessageTool(reader),
        OpenClawBrowserTool(reader),
        OpenClawMemoryTool(reader),
        OpenClawDevicesTool(reader),
        OpenClawCronTool(reader),
    ]
