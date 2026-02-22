"""Session manager with PreparedBundle LRU cache and session lifecycle handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from amplifier_foundation import Bundle, load_bundle
from amplifier_foundation.mentions import BaseMentionResolver

from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
from amplifier_app_openclaw.adapters.display import OpenClawDisplaySystem
from amplifier_app_openclaw.adapters.streaming import OpenClawStreamingHook
from amplifier_app_openclaw.cost import CostEntry, generate_cost_report, log_cost_entry
from amplifier_app_openclaw.governance import GovernanceEngine
from amplifier_app_openclaw.rpc import (
    BUNDLE_ERROR,
    CANCELLED_ERROR,
    JsonRpcResponseReader,
    JsonRpcWriter,
    SESSION_ERROR,
)
from amplifier_app_openclaw.runner import CHAT_OVERLAY
from amplifier_app_openclaw.spawn import OpenClawSpawnManager
from amplifier_app_openclaw.tools import create_openclaw_tools

logger = logging.getLogger(__name__)

# Defaults (Pi-friendly)
_DEFAULT_MAX_BUNDLES = 2
_DEFAULT_MAX_SESSIONS = 2


@dataclass
class SessionState:
    """Tracks all state for one active session."""

    session: Any  # AmplifierSession
    approval_system: OpenClawApprovalSystem
    streaming_hook: OpenClawStreamingHook
    spawn_manager: OpenClawSpawnManager
    display_system: OpenClawDisplaySystem
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """Manages PreparedBundle cache and active sessions.

    Provides JSON-RPC handlers for session/create, session/execute,
    session/cancel, session/cleanup, and session/list.
    """

    def __init__(self, writer: JsonRpcWriter, response_reader: JsonRpcResponseReader) -> None:
        self._writer = writer
        self._response_reader = response_reader

        self._max_bundles = int(os.environ.get("AMPLIFIER_MAX_BUNDLES", _DEFAULT_MAX_BUNDLES))
        self._max_sessions = int(os.environ.get("AMPLIFIER_MAX_SESSIONS", _DEFAULT_MAX_SESSIONS))

        # LRU cache: bundle_name -> PreparedBundle (OrderedDict for LRU)
        self._bundle_cache: OrderedDict[str, Any] = OrderedDict()

        # Active sessions: session_id -> SessionState
        self._sessions: dict[str, SessionState] = {}

        # Governance engine for tool evaluation
        self._governance = GovernanceEngine()

    # -- Bundle cache ----------------------------------------------------------

    async def _get_or_prepare_bundle(self, bundle_name: str) -> Any:
        """Return a cached PreparedBundle or load+prepare and cache it."""
        if bundle_name in self._bundle_cache:
            # LRU touch
            self._bundle_cache.move_to_end(bundle_name)
            logger.info("Bundle cache hit: %s", bundle_name)
            return self._bundle_cache[bundle_name]

        # Evict LRU if at capacity
        while len(self._bundle_cache) >= self._max_bundles:
            evicted_name, _ = self._bundle_cache.popitem(last=False)
            logger.info("Bundle cache eviction: %s", evicted_name)

        bundle = await load_bundle(bundle_name)
        bundle = CHAT_OVERLAY.compose(bundle)
        prepared = await bundle.prepare(install_deps=True)

        # Inject user-configured providers from ~/.amplifier/settings.yaml
        from amplifier_app_openclaw.runner import _inject_user_providers
        _inject_user_providers(prepared)

        self._bundle_cache[bundle_name] = prepared
        logger.info("Bundle cached: %s (cache size: %d/%d)", bundle_name, len(self._bundle_cache), self._max_bundles)
        return prepared

    # -- JSON-RPC handlers -----------------------------------------------------

    async def handle_create(self, params: dict[str, Any]) -> Any:
        """Handle ``session/create``.

        Params:
            bundle: str — bundle name/path
            cwd: str (optional) — session working directory

        Returns:
            session_id, agents (list), tools (list)
        """
        bundle_name = params.get("bundle", "")
        if not bundle_name:
            raise ValueError("Missing required param: bundle")

        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(
                f"Max concurrent sessions reached ({self._max_sessions}). "
                "Clean up an existing session first."
            )

        session_id = str(uuid.uuid4())
        cwd = params.get("cwd", str(Path.cwd()))

        # Load / cache bundle
        prepared = await self._get_or_prepare_bundle(bundle_name)

        # Create adapters
        approval_system = OpenClawApprovalSystem(session_id, self._writer)
        display_system = OpenClawDisplaySystem(session_id, self._writer)

        # Create session
        session = await prepared.create_session(
            session_id=session_id,
            approval_system=approval_system,
            display_system=display_system,
            session_cwd=Path(cwd),
        )

        # Register streaming hook
        streaming_hook = OpenClawStreamingHook(session_id, self._writer)
        streaming_hook.register(session)

        # Register mention resolver
        resolver = BaseMentionResolver(base_path=Path(cwd))
        session.coordinator.register_capability("mention_resolver", resolver)

        # Register spawn manager
        spawn_manager = OpenClawSpawnManager(prepared, session_id, self._writer)
        spawn_manager.register(session.coordinator)

        # Mount OpenClaw tools
        tools = create_openclaw_tools(self._writer, self._response_reader)
        for tool in tools:
            await session.coordinator.mount("tools", tool, name=tool.name)

        # Track session
        self._sessions[session_id] = SessionState(
            session=session,
            approval_system=approval_system,
            streaming_hook=streaming_hook,
            spawn_manager=spawn_manager,
            display_system=display_system,
            metadata={
                "bundle": bundle_name,
                "cwd": cwd,
                "created_at": time.time(),
                "status": "ready",
            },
        )

        # Collect available agents and tools
        agents = list(session.config.get("agents", {}).keys())
        tool_names = list(session.coordinator.mount_points.get("tools", {}).keys())

        return {
            "session_id": session_id,
            "agents": agents,
            "tools": tool_names,
        }

    async def handle_execute(self, params: dict[str, Any]) -> Any:
        """Handle ``session/execute``.

        Params:
            session_id: str
            prompt: str
            timeout: int (optional, default 300)

        Returns:
            response, usage, status
        """
        session_id = params.get("session_id", "")
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        prompt = params.get("prompt", "")
        if not prompt:
            raise ValueError("Missing required param: prompt")

        timeout = params.get("timeout", 300)
        state.metadata["status"] = "executing"
        start_time = time.time()

        try:
            response = await asyncio.wait_for(
                state.session.execute(prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await state.session.coordinator.cancellation.request_graceful()
            await asyncio.sleep(2)
            await state.session.coordinator.cancellation.request_immediate()
            state.metadata["status"] = "timeout"
            raise RuntimeError(f"Execution timed out after {timeout}s")

        duration = time.time() - start_time
        status_obj = state.session.status
        state.metadata["status"] = status_obj.status if status_obj else "completed"

        input_tokens = getattr(status_obj, "total_input_tokens", 0)
        output_tokens = getattr(status_obj, "total_output_tokens", 0)
        estimated_cost = getattr(status_obj, "estimated_cost", 0.0) or 0.0

        # Log cost entry
        try:
            log_cost_entry(CostEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=session_id,
                bundle=state.metadata.get("bundle", ""),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                duration_seconds=round(duration, 2),
                task_summary=prompt[:200],
            ))
        except Exception:
            logger.exception("Failed to log cost entry")

        return {
            "response": response,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost": estimated_cost,
                "tool_invocations": getattr(status_obj, "tool_invocations", 0),
            },
            "status": state.metadata["status"],
        }

    async def handle_cancel(self, params: dict[str, Any]) -> Any:
        """Handle ``session/cancel``.

        Params:
            session_id: str
            immediate: bool (optional, default false)
        """
        session_id = params.get("session_id", "")
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        immediate = params.get("immediate", False)
        cancellation = state.session.coordinator.cancellation

        if immediate:
            await cancellation.request_immediate()
        else:
            await cancellation.request_graceful()

        state.metadata["status"] = "cancelling"
        return {"session_id": session_id, "status": "cancelling"}

    async def handle_cleanup(self, params: dict[str, Any]) -> Any:
        """Handle ``session/cleanup``.

        Params:
            session_id: str
        """
        session_id = params.get("session_id", "")
        state = self._sessions.pop(session_id, None)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        # Unregister hooks
        state.streaming_hook.unregister()

        # Cleanup session
        try:
            await state.session.cleanup()
        except Exception:
            logger.exception("Error during session cleanup for %s", session_id)

        return {"session_id": session_id, "status": "cleaned_up"}

    async def handle_list(self, params: dict[str, Any]) -> Any:
        """Handle ``session/list``. Returns all active sessions."""
        sessions = []
        for sid, state in self._sessions.items():
            sessions.append({
                "session_id": sid,
                "bundle": state.metadata.get("bundle", ""),
                "status": state.metadata.get("status", "unknown"),
                "created_at": state.metadata.get("created_at"),
            })
        return {"sessions": sessions}

    # -- Approval response handler ---------------------------------------------

    async def handle_approval_response(self, params: dict[str, Any]) -> Any:
        """Handle ``session/approval_response``.

        Params:
            session_id: str
            request_id: str
            selected_option: str
        """
        session_id = params.get("session_id", "")
        request_id = params.get("request_id", "")
        selected_option = params.get("selected_option", "")

        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        state.approval_system.resolve_approval(request_id, selected_option)
        return {"status": "ok"}

    # -- Bundle handlers -------------------------------------------------------

    async def handle_bundle_list(self, params: dict[str, Any]) -> Any:
        """Handle ``bundle/list``. Returns cached bundles."""
        bundles = []
        for name in self._bundle_cache:
            bundles.append({
                "name": name,
                "status": "cached",
            })
        return {"bundles": bundles}

    async def handle_bundle_add(self, params: dict[str, Any]) -> Any:
        """Handle ``bundle/add``. Loads and caches a bundle.

        Params:
            bundle: str — bundle name or git URL
        """
        bundle_name = params.get("bundle", "")
        if not bundle_name:
            raise ValueError("Missing required param: bundle")

        try:
            prepared = await self._get_or_prepare_bundle(bundle_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to load bundle '{bundle_name}': {exc}") from exc

        return {"status": "ok", "bundle": bundle_name}

    # -- Governance handler ----------------------------------------------------

    async def handle_evaluate_tool(self, params: dict[str, Any]) -> Any:
        """Handle ``augment/evaluate_tool``.

        Params:
            tool: str — tool name
            input: str|dict — tool input/arguments
            context: dict (optional)
        """
        tool = params.get("tool", "")
        input_text = params.get("input", "")
        context = params.get("context")
        return self._governance.evaluate(tool, input_text, context)

    # -- Cost report handler ---------------------------------------------------

    async def handle_cost_report(self, params: dict[str, Any]) -> Any:
        """Handle ``augment/cost_report``.

        Params:
            period: str (optional, default "day") — "day"|"week"|"month"|"all"
            session_id: str (optional) — filter by session
        """
        period = params.get("period", "day")
        session_id = params.get("session_id")
        return generate_cost_report(period=period, session_id=session_id)

    async def handle_query_context(self, params: dict[str, Any]) -> Any:
        """Handle ``augment/query_context``.

        Creates a lightweight ephemeral session with the foundation bundle,
        executes the query, and returns the response. Simplified version for
        Phase 1 — Phase 1.5 adds agent-specific routing.

        Params:
            query: str — the question to answer
            bundle: str (optional, default "foundation") — bundle to use
        """
        query = params.get("query")
        if not query:
            return {"error": "query is required"}
        bundle_name = params.get("bundle", "foundation")
        try:
            result = await self.handle_create({"bundle": bundle_name, "cwd": "."})
            session_id = result["session_id"]
            try:
                exec_result = await self.handle_execute({
                    "session_id": session_id,
                    "prompt": query,
                    "timeout": 60,
                })
                return {
                    "response": exec_result.get("response", ""),
                    "source": bundle_name,
                    "usage": exec_result.get("usage", {}),
                }
            finally:
                await self.handle_cleanup({"session_id": session_id})
        except Exception as exc:
            return {"error": str(exc), "source": bundle_name}

    # -- Clean shutdown --------------------------------------------------------

    async def cleanup_all(self) -> None:
        """Clean up all active sessions. Called during shutdown."""
        for session_id in list(self._sessions.keys()):
            try:
                await self.handle_cleanup({"session_id": session_id})
            except Exception:
                logger.exception("Error cleaning up session %s during shutdown", session_id)

    # -- Registration helper ---------------------------------------------------

    def register_handlers(self, rpc_reader: Any) -> None:
        """Register all JSON-RPC handlers on the given JsonRpcReader."""
        # Session lifecycle
        rpc_reader.register("session/create", self.handle_create)
        rpc_reader.register("session/execute", self.handle_execute)
        rpc_reader.register("session/cancel", self.handle_cancel)
        rpc_reader.register("session/cleanup", self.handle_cleanup)
        rpc_reader.register("session/list", self.handle_list)
        rpc_reader.register("session/approval_response", self.handle_approval_response)

        # Bundle management
        rpc_reader.register("bundle/list", self.handle_bundle_list)
        rpc_reader.register("bundle/add", self.handle_bundle_add)

        # Augmentation
        rpc_reader.register("augment/evaluate_tool", self.handle_evaluate_tool)
        rpc_reader.register("augment/cost_report", self.handle_cost_report)
        rpc_reader.register("augment/query_context", self.handle_query_context)
