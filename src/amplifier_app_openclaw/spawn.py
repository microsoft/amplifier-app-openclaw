"""CLI spawn manager — stub for Phase 0."""

from __future__ import annotations

from typing import Any


class CLISpawnManager:
    """Stub spawn manager for Phase 0 (CLI mode).

    Agent delegation (spawn/resume) requires the Phase 1 sidecar for proper
    multi-session management. In Phase 0, these operations raise
    NotImplementedError with a clear message.
    """

    def __init__(self, prepared: Any) -> None:
        self._prepared = prepared

    async def spawn(self, **kwargs: Any) -> dict[str, Any]:
        """Spawn a child session for agent delegation.

        Not available in Phase 0 CLI mode. Will be implemented in Phase 1
        with the JSON-RPC sidecar.
        """
        raise NotImplementedError(
            "Agent delegation (spawn) is not available in CLI mode (Phase 0). "
            "Use 'amplifier-openclaw serve' (Phase 1) for multi-agent delegation."
        )

    async def resume(self, **kwargs: Any) -> dict[str, Any]:
        """Resume a previously spawned child session.

        Not available in Phase 0 CLI mode. Will be implemented in Phase 1
        with the JSON-RPC sidecar.
        """
        raise NotImplementedError(
            "Session resume is not available in CLI mode (Phase 0). "
            "Use 'amplifier-openclaw serve' (Phase 1) for session persistence."
        )
