"""Bidirectional capability discovery between OpenClaw and Amplifier."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from amplifier_app_openclaw.rpc import JsonRpcResponseReader, JsonRpcWriter

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """Describes a single tool's capabilities."""

    name: str
    description: str
    input_schema: dict[str, Any]


async def discover_openclaw_tools(rpc_reader: JsonRpcResponseReader) -> list[ToolSpec]:
    """Query OpenClaw for available tools via JSON-RPC.

    Returns an empty list if OpenClaw doesn't support the method yet.
    """
    try:
        result = await rpc_reader.request("openclaw/tools_list", {})
        return [ToolSpec(**t) for t in result.get("tools", [])]
    except Exception:
        logger.debug("openclaw/tools_list not available; returning empty list")
        return []


def list_session_tools(session: Any) -> list[dict[str, Any]]:
    """List all tools available in an Amplifier session.

    Reads from coordinator.mount_points["tools"].
    """
    tools = session.coordinator.mount_points.get("tools") or {}
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools.values()
    ]


def register_amplifier_tools(
    writer: JsonRpcWriter, session_id: str, tools: list[dict[str, Any]]
) -> None:
    """Register Amplifier's tools with OpenClaw via JSON-RPC notification."""
    writer.notify(
        "amplifier/tools_available",
        {
            "session_id": session_id,
            "tools": tools,
        },
    )
