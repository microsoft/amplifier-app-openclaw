"""StreamingHook adapter — forwards session lifecycle events as JSON-RPC notifications."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from amplifier_core.models import HookResult

from amplifier_app_openclaw.rpc import JsonRpcWriter

logger = logging.getLogger(__name__)

# Every event we forward.  Per-event registration (no wildcards).
FORWARDED_EVENTS: list[str] = [
    "content_block:start",
    "content_block:delta",
    "content_block:end",
    "tool:pre",
    "tool:post",
    "tool:error",
    "session:fork",
    "plan:start",
    "plan:end",
    "thinking:delta",
    "thinking:final",
    "provider:request",
    "provider:response",
]

_MAX_STR_LEN = 10_000
_MAX_LIST_LEN = 100


def _sanitize(obj: Any) -> Any:
    """Truncate large strings and cap list lengths for safe serialisation."""
    if isinstance(obj, str):
        return obj[:_MAX_STR_LEN] if len(obj) > _MAX_STR_LEN else obj
    if isinstance(obj, list):
        items = obj[:_MAX_LIST_LEN]
        return [_sanitize(i) for i in items]
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    return obj


class OpenClawStreamingHook:
    """Registers per-event hooks that forward events as ``session/event`` notifications."""

    def __init__(self, session_id: str, writer: JsonRpcWriter) -> None:
        self._session_id = session_id
        self._writer = writer
        self._unregisters: list[Callable[[], None]] = []

    # -- handler factory -------------------------------------------------------

    def _make_handler(self, event_name: str):
        """Return an async handler for *event_name*."""

        async def _handler(_event: str, data: dict[str, Any]) -> HookResult:
            try:
                sanitized = _sanitize(data)
                # Verify JSON-serialisable before sending
                json.dumps(sanitized, default=str)
                self._writer.notify(
                    "session/event",
                    {
                        "session_id": self._session_id,
                        "type": event_name,
                        "data": sanitized,
                    },
                )
            except Exception:
                logger.exception("streaming hook: failed to forward %s", event_name)
            return HookResult(action="continue")

        _handler.__name__ = f"openclaw_stream_{event_name}"
        return _handler

    # -- public API ------------------------------------------------------------

    def register(self, session: Any) -> None:
        """Register handlers for all forwarded events on *session*'s hook registry."""
        hooks = session.coordinator.hooks
        for event in FORWARDED_EVENTS:
            unreg = hooks.register(
                event,
                self._make_handler(event),
                priority=100,  # low priority — observe only
                name=f"openclaw_stream_{event}",
            )
            self._unregisters.append(unreg)
        logger.debug(
            "StreamingHook: registered %d event handlers for session %s",
            len(self._unregisters),
            self._session_id,
        )

    def unregister(self) -> None:
        """Remove all previously registered hooks."""
        for unreg in self._unregisters:
            try:
                unreg()
            except Exception:
                logger.exception("streaming hook: error during unregister")
        self._unregisters.clear()
        logger.debug("StreamingHook: unregistered all handlers for session %s", self._session_id)
