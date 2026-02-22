"""
OpenClaw ApprovalSystem adapter.

Sends approval requests as JSON-RPC ``session/approval`` notifications and waits
for ``session/approval_response`` to resolve them via asyncio futures.

NOTE (S6): Approval blocks execution and counts against the overall execution
timeout. The time spent waiting for an approval response is *not* paused or
budgeted separately — it consumes the same wall-clock budget as tool execution.
Consider separate timeout budgets in a future phase.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

from amplifier_app_openclaw.rpc import JsonRpcWriter

logger = logging.getLogger(__name__)


class OpenClawApprovalSystem:
    """ApprovalSystem implementation that routes approvals over JSON-RPC."""

    def __init__(self, session_id: str, writer: JsonRpcWriter) -> None:
        self._session_id = session_id
        self._writer = writer
        self._pending: dict[str, asyncio.Future[str]] = {}

    async def request_approval(
        self,
        prompt: str,
        options: list[str],
        timeout: float,
        default: Literal["allow", "deny"],
    ) -> str:
        """Send an approval notification and block until resolved or timed out.

        On timeout, returns *default* rather than raising, matching the protocol
        expectation that the caller always gets a usable option back.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        self._writer.notify(
            "session/approval",
            {
                "session_id": self._session_id,
                "request_id": request_id,
                "prompt": prompt,
                "options": options,
                "timeout": timeout,
                "default": default,
            },
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Approval request %s timed out after %.1fs, returning default '%s'",
                request_id,
                timeout,
                default,
            )
            return default
        finally:
            self._pending.pop(request_id, None)

    def resolve_approval(self, request_id: str, selected_option: str) -> None:
        """Resolve a pending approval future with the selected option.

        Called by the JSON-RPC reader when a ``session/approval_response``
        message arrives from OpenClaw.
        """
        future = self._pending.get(request_id)
        if future is None:
            logger.warning("resolve_approval called for unknown request_id: %s", request_id)
            return
        if future.done():
            logger.warning("resolve_approval called for already-resolved request_id: %s", request_id)
            return
        future.set_result(selected_option)
