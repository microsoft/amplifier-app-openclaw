"""Spawn managers for CLI (Phase 0) and OpenClaw sidecar (Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle
    from amplifier_app_openclaw.rpc import JsonRpcWriter


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


class OpenClawSpawnManager:
    """Phase 1 spawn manager using PreparedBundle.spawn() for child sessions.

    Registers both ``session.spawn`` and ``session.resume`` capabilities on the
    coordinator so orchestrator delegation tools can invoke them.

    PreparedBundle.spawn() returns a dict, NOT an AmplifierSession.
    Child sessions are created, used, and cleaned up within spawn().
    """

    def __init__(
        self,
        prepared: PreparedBundle,
        session_id: str,
        writer: JsonRpcWriter,
    ) -> None:
        self._prepared = prepared
        self._session_id = session_id
        self._writer = writer

    def register(self, coordinator: Any) -> None:
        """Register ``session.spawn`` and ``session.resume`` on *coordinator*."""
        coordinator.register_capability("session.spawn", self.spawn)
        coordinator.register_capability("session.resume", self.resume)

    async def spawn(self, config: dict[str, Any]) -> dict[str, Any]:
        """Spawn a child session. Registered as ``session.spawn`` capability.

        Forwards ALL parameters to ``PreparedBundle.spawn()``.
        Returns dict with: output, session_id, status, turn_count, metadata.
        """
        from amplifier_foundation import load_bundle

        # Resolve child bundle
        child_bundle_name = config.get("bundle")
        if child_bundle_name:
            child_bundle = await load_bundle(child_bundle_name)
        else:
            child_bundle = self._prepared.bundle

        # Forward all spawn parameters to PreparedBundle.spawn()
        result = await self._prepared.spawn(
            child_bundle=child_bundle,
            instruction=config["instruction"],
            compose=config.get("compose", True),
            parent_session=config.get("parent_session"),
            session_id=config.get("session_id"),
            orchestrator_config=config.get("orchestrator_config"),
            parent_messages=config.get("parent_messages"),
            session_cwd=Path(config["session_cwd"]) if config.get("session_cwd") else None,
            provider_preferences=config.get("provider_preferences"),
            self_delegation_depth=config.get("self_delegation_depth", 0),
        )

        # PreparedBundle.spawn() returns a dict — ensure expected keys
        if not isinstance(result, dict):
            result = {"output": str(result)}

        result.setdefault("session_id", config.get("session_id", ""))
        result.setdefault("output", "")
        result.setdefault("status", "completed")
        result.setdefault("turn_count", 0)
        result.setdefault("metadata", {})

        return result

    async def resume(self, config: dict[str, Any]) -> dict[str, Any]:
        """Resume a previous child session. Not yet supported (Phase 1.5)."""
        return {
            "output": "Session resume is not yet supported in the OpenClaw sidecar. "
            "This will be available in Phase 1.5.",
            "session_id": config.get("session_id", "unknown"),
            "status": "failed",
            "turn_count": 0,
            "metadata": {},
        }
