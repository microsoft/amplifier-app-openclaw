"""Session manager with PreparedBundle LRU cache and session lifecycle handlers."""

from __future__ import annotations

import asyncio
import copy
import hashlib
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
from amplifier_app_openclaw.injection import InjectionManager
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

# Persistent session storage root
_PERSISTENT_SESSIONS_DIR = Path.home() / ".openclaw" / "amplifier" / "sessions"


def _deterministic_session_id(bundle_name: str, session_name: str) -> str:
    """Generate a deterministic session ID from bundle + session name."""
    return hashlib.sha256(f"{bundle_name}:{session_name}".encode()).hexdigest()[:16]


def _context_persistent_available() -> bool:
    """Check if context-persistent module is importable."""
    try:
        import amplifier_module_context_persistent  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class SessionState:
    """Tracks all state for one active session."""

    session: Any  # AmplifierSession
    approval_system: OpenClawApprovalSystem
    streaming_hook: OpenClawStreamingHook
    spawn_manager: OpenClawSpawnManager
    display_system: OpenClawDisplaySystem
    injection_manager: InjectionManager = field(default_factory=InjectionManager)
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

        # Query result cache
        from amplifier_app_openclaw.context_router import QueryCache
        self._query_cache = QueryCache()

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
            session_name: str (optional) — named session for deterministic ID
            persistent: bool (optional) — enable session persistence
            session_id: str (optional) — explicit session ID (for resume)
            _is_resumed: bool (internal) — whether this is a resumed session

        Returns:
            session_id, agents (list), tools (list), persistent (bool), resumed (bool)
        """
        bundle_name = params.get("bundle", "")
        if not bundle_name:
            raise ValueError("Missing required param: bundle")

        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(
                f"Max concurrent sessions reached ({self._max_sessions}). "
                "Clean up an existing session first."
            )

        persistent = params.get("persistent", False)
        session_name = params.get("session_name")
        is_resumed = params.get("_is_resumed", False)

        # Determine session ID
        if params.get("session_id"):
            session_id = params["session_id"]
        elif session_name:
            session_id = _deterministic_session_id(bundle_name, session_name)
        else:
            session_id = str(uuid.uuid4())

        cwd = params.get("cwd", str(Path.cwd()))

        # Load / cache bundle
        prepared = await self._get_or_prepare_bundle(bundle_name)

        # Create adapters
        approval_system = OpenClawApprovalSystem(session_id, self._writer)
        display_system = OpenClawDisplaySystem(session_id, self._writer)

        # Determine if we should use persistence
        session_dir = _PERSISTENT_SESSIONS_DIR / session_id
        transcript_path = session_dir / "context-messages.jsonl"

        if persistent and not _context_persistent_available():
            logger.warning("context-persistent module not available; falling back to non-persistent")
            persistent = False

        # Auto-detect resume: if persistent and transcript already exists
        if persistent and not is_resumed and transcript_path.exists():
            is_resumed = True
            logger.info("Existing session storage found for %s; resuming", session_id)

        if persistent:
            # Deep-copy mount_plan so we don't mutate the cached PreparedBundle
            mount_plan = copy.deepcopy(prepared.mount_plan)
            session_section = mount_plan.get("session", {})
            session_section["context"] = {
                "module": "context-persistent",
                "config": {
                    "transcript_path": str(transcript_path),
                    "max_tokens": 200000,
                },
            }
            mount_plan["session"] = session_section

            # Create session directly with modified mount_plan
            from amplifier_core import AmplifierSession

            session = AmplifierSession(
                mount_plan,
                session_id=session_id,
                approval_system=approval_system,
                display_system=display_system,
                is_resumed=is_resumed,
            )
            await session.coordinator.mount("module-source-resolver", prepared.resolver)

            if hasattr(prepared, "bundle_package_paths") and prepared.bundle_package_paths:
                session.coordinator.register_capability(
                    "bundle_package_paths", list(prepared.bundle_package_paths)
                )

            await session.initialize()
        else:
            # Standard non-persistent path
            session = await prepared.create_session(
                session_id=session_id,
                approval_system=approval_system,
                display_system=display_system,
                session_cwd=Path(cwd),
                is_resumed=is_resumed,
            )

        # Register a hook to accumulate token usage from provider responses
        from amplifier_core.hooks import HookResult

        async def _track_usage(event: str, data: dict[str, Any]) -> HookResult:
            usage = data.get("usage", {})
            session.status.total_input_tokens += (
                usage.get("input", 0)
                + usage.get("cache_read", 0)
                + usage.get("cache_write", 0)
            )
            session.status.total_output_tokens += usage.get("output", 0)
            return HookResult(action="continue", data=data)

        session.coordinator.hooks.register("llm:response", _track_usage)

        # Register streaming hook
        streaming_hook = OpenClawStreamingHook(session_id, self._writer)
        streaming_hook.register(session)

        # Register injection hook for mid-execution message injection
        injection_manager = InjectionManager()
        session.coordinator.hooks.register(
            "provider:request",
            injection_manager.hook_handler,
            name="injection_manager",
        )

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
            injection_manager=injection_manager,
            metadata={
                "bundle": bundle_name,
                "cwd": cwd,
                "created_at": time.time(),
                "status": "ready",
                "persistent": persistent,
                "session_name": session_name,
                "resumed": is_resumed,
            },
        )

        # Collect available agents and tools
        agents = list(session.config.get("agents", {}).keys())
        tool_names = list(session.coordinator.mount_points.get("tools", {}).keys())

        return {
            "session_id": session_id,
            "agents": agents,
            "tools": tool_names,
            "persistent": persistent,
            "resumed": is_resumed,
        }

    async def handle_resume(self, params: dict[str, Any]) -> Any:
        """Handle ``session/resume``.

        Convenience method to resume a previously-persisted session.

        Params:
            session_id: str — the session ID to resume
            bundle: str — bundle name/path
            cwd: str (optional)

        Returns:
            Same as session/create with resumed=True.
        """
        session_id = params.get("session_id", "")
        bundle = params.get("bundle", "")
        if not session_id:
            raise ValueError("Missing required param: session_id")
        if not bundle:
            raise ValueError("Missing required param: bundle")

        session_dir = _PERSISTENT_SESSIONS_DIR / session_id
        if not (session_dir / "context-messages.jsonl").exists():
            raise ValueError(f"No saved session found: {session_id}")

        return await self.handle_create({
            "bundle": bundle,
            "session_id": session_id,
            "persistent": True,
            "_is_resumed": True,
            "cwd": params.get("cwd", str(Path.cwd())),
        })

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

    async def handle_inject(self, params: dict[str, Any]) -> Any:
        """Handle ``session/inject``.

        Enqueue a user message for injection into the next LLM call of an
        actively executing session.

        Params:
            session_id: str
            message: str — text to inject

        Returns:
            status, session_id
        """
        session_id = params.get("session_id", "")
        message = params.get("message", "")
        if not message:
            raise ValueError("Missing required param: message")

        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        if state.metadata.get("status") != "executing":
            raise RuntimeError("Session not currently executing")

        await state.injection_manager.inject(message)
        return {"status": "injected", "session_id": session_id}

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

    def _find_session_by_bundle(self, bundle_name: str) -> str | None:
        """Find an existing ready session using the given bundle."""
        for sid, state in self._sessions.items():
            if (state.metadata.get("bundle") == bundle_name
                    and state.metadata.get("status") == "ready"):
                return sid
        return None

    async def handle_query_context(self, params: dict[str, Any]) -> Any:
        """Handle ``augment/query_context``.

        Routes queries to appropriate agents/bundles based on query type,
        reuses existing sessions when possible, and caches results.

        Params:
            query: str — the question to answer
            bundle: str (optional) — override bundle (skips routing)
        """
        query = params.get("query")
        if not query:
            return {"error": "query is required"}

        # Check cache first
        cached = self._query_cache.get(query)
        if cached is not None:
            return {**cached, "cached": True}

        # Route the query
        from amplifier_app_openclaw.context_router import route_query
        bundle_name, agent_hint = route_query(query)

        # Override with explicit bundle if provided
        bundle_name = params.get("bundle", bundle_name)

        # Local handling for cost queries
        if bundle_name is None:
            return await self.handle_cost_report(params)

        # Try to reuse an existing session with matching bundle
        existing = self._find_session_by_bundle(bundle_name)
        if existing:
            result = await self.handle_execute({
                "session_id": existing,
                "prompt": query,
                "timeout": 60,
            })
            response = {
                "response": result.get("response", ""),
                "source": bundle_name,
                "agent": agent_hint,
                "usage": result.get("usage", {}),
                "reused_session": True,
            }
            self._query_cache.put(query, response)
            return response

        # Create ephemeral session
        try:
            result = await self.handle_create({"bundle": bundle_name, "cwd": "."})
            session_id = result["session_id"]
            try:
                exec_result = await self.handle_execute({
                    "session_id": session_id,
                    "prompt": query,
                    "timeout": 60,
                })
                response = {
                    "response": exec_result.get("response", ""),
                    "source": bundle_name,
                    "agent": agent_hint,
                    "usage": exec_result.get("usage", {}),
                }
                self._query_cache.put(query, response)
                return response
            finally:
                await self.handle_cleanup({"session_id": session_id})
        except Exception as exc:
            # Fallback to foundation if preferred bundle fails
            if bundle_name != "foundation":
                return await self.handle_query_context({
                    "query": query, "bundle": "foundation"
                })
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
        rpc_reader.register("session/resume", self.handle_resume)
        rpc_reader.register("session/execute", self.handle_execute)
        rpc_reader.register("session/inject", self.handle_inject)
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
