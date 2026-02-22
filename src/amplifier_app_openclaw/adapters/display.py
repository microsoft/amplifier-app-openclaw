"""OpenClaw display system — routes show_message() to JSON-RPC notifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from amplifier_app_openclaw.rpc import JsonRpcWriter


class OpenClawDisplaySystem:
    """DisplaySystem implementation that sends ``session/display`` notifications over JSON-RPC."""

    def __init__(self, session_id: str, writer: JsonRpcWriter) -> None:
        self._session_id = session_id
        self._writer = writer

    def show_message(
        self,
        message: str,
        level: Literal["info", "warning", "error"] = "info",
        source: str = "hook",
    ) -> None:
        self._writer.notify(
            "session/display",
            {
                "session_id": self._session_id,
                "message": message,
                "level": level,
                "source": source,
            },
        )
