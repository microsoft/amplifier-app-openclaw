"""Mid-execution message injection for active sessions.

Provides an ``InjectionManager`` per session that queues user messages and
delivers them to the orchestrator via a ``provider:request`` hook.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from amplifier_core.models import HookResult

logger = logging.getLogger(__name__)


class InjectionManager:
    """Per-session injection queue.

    Messages are enqueued via :meth:`inject` (called from the
    ``session/inject`` RPC handler) and drained by :meth:`hook_handler`
    which is registered on the ``provider:request`` event.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def inject(self, message: str) -> None:
        """Enqueue a user message for injection into the next LLM call."""
        await self._queue.put(message)
        logger.info("Injection queued (%d pending)", self._queue.qsize())

    async def hook_handler(self, event: str, data: dict[str, Any]) -> HookResult:
        """``provider:request`` hook — drain queued messages into context."""
        injections: list[str] = []
        while not self._queue.empty():
            try:
                injections.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if injections:
            combined = "\n\n".join(
                f"[User interjection]: {m}" for m in injections
            )
            logger.info("Injecting %d message(s) into context", len(injections))
            return HookResult(
                action="inject_context",
                context_injection=combined,
                context_injection_role="user",
                ephemeral=True,
            )

        return HookResult(action="continue", data=data)
