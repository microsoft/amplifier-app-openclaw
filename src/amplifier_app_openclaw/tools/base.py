"""Base class for OpenClaw tool bridges."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from amplifier_core import ToolResult

from amplifier_app_openclaw.rpc import JsonRpcError, JsonRpcResponseReader

logger = logging.getLogger(__name__)


class OpenClawToolBase(ABC):
    """Abstract base for tools that bridge Amplifier agents to OpenClaw capabilities.

    Subclasses define name, description, and input_schema. The execute() method
    sends an ``openclaw/tool_call`` JSON-RPC **request** (with id) to the OpenClaw
    gateway and awaits the correlated response.
    """

    def __init__(self, rpc: JsonRpcResponseReader) -> None:
        self._rpc = rpc

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Send openclaw/tool_call request and return the result."""
        try:
            result = await self._rpc.request(
                "openclaw/tool_call",
                {"tool": self.name, "input": input},
            )
            return ToolResult(success=True, output=result)
        except JsonRpcError as exc:
            logger.warning("Tool %s failed: %s", self.name, exc)
            return ToolResult(
                success=False,
                error={"code": exc.code, "message": exc.rpc_message, "data": exc.data},
            )
        except Exception as exc:
            logger.exception("Unexpected error executing tool %s", self.name)
            return ToolResult(success=False, error={"message": str(exc)})
