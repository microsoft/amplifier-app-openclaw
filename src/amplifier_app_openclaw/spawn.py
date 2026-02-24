"""Spawn managers for CLI and OpenClaw sidecar modes.

CLISpawnManager creates child AmplifierSession instances in-process for agent
delegation and recipe sub-steps.  OpenClawSpawnManager forwards to the
sidecar's PreparedBundle.spawn() API.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_foundation.bundle import PreparedBundle
    from amplifier_app_openclaw.rpc import JsonRpcWriter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config merge utilities (private)
#
# Ported from amplifier-app-cli's agent_config / merge_utils modules.
# Kept private here because they are implementation details of spawning.
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two dicts.  *overlay* wins on conflicts; arrays are replaced."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merge_module_lists(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge module lists by ``module`` key.  Overlay configs deep-merge."""
    base_by_key: dict[str, dict[str, Any]] = {}
    for item in base:
        key = item.get("module")
        if key:
            base_by_key[key] = item.copy()
    for item in overlay:
        key = item.get("module")
        if key and key in base_by_key:
            base_by_key[key] = _deep_merge(base_by_key[key], item)
        elif key:
            base_by_key[key] = item.copy()
    return list(base_by_key.values())


def _merge_agent_dicts(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge *child* into *parent*.  Module lists merge by module ID."""
    merged = parent.copy()
    for key, child_value in child.items():
        if key not in merged:
            merged[key] = child_value
        elif key in ("hooks", "tools", "providers"):
            merged[key] = _merge_module_lists(merged[key], child_value)
        elif isinstance(child_value, dict) and isinstance(merged[key], dict):
            merged[key] = _deep_merge(merged[key], child_value)
        else:
            merged[key] = child_value
    return merged


def _apply_spawn_tool_policy(parent: dict[str, Any]) -> dict[str, Any]:
    """Filter parent tools per ``spawn.exclude_tools`` / ``spawn.tools``."""
    spawn_config = parent.get("spawn", {})
    if not spawn_config:
        return parent

    filtered = parent.copy()
    parent_tools = parent.get("tools", [])

    # Explicit spawn.tools replaces inheritance entirely
    if "tools" in spawn_config:
        spawn_tools = spawn_config["tools"]
        if isinstance(spawn_tools, list):
            filtered["tools"] = spawn_tools
        return filtered

    # Blocklist mode
    exclude = spawn_config.get("exclude_tools", [])
    if exclude and isinstance(exclude, list):
        filtered["tools"] = [
            t for t in parent_tools if t.get("module") not in exclude
        ]
    return filtered


def _merge_configs(
    parent: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Merge parent session config with agent overlay.

    Applies spawn tool policy, filters agent access, then deep-merges
    module lists by module ID.
    """
    filtered_parent = _apply_spawn_tool_policy(parent)

    overlay_copy = overlay.copy()
    agent_filter = overlay_copy.pop("agents", None)

    result = _merge_agent_dicts(filtered_parent, overlay_copy)

    # Smart Single Value for agent access control
    if agent_filter == "none":
        result["agents"] = {}
    elif isinstance(agent_filter, list):
        parent_agents = parent.get("agents", {})
        result["agents"] = {
            k: v for k, v in parent_agents.items() if k in agent_filter
        }

    return result


def _filter_tools(
    config: dict[str, Any],
    policy: dict[str, list[str]],
) -> dict[str, Any]:
    """Filter tools per inheritance policy (exclude list or allow list)."""
    tools = config.get("tools", [])
    if not tools:
        return config

    exclude = policy.get("exclude_tools", [])
    inherit = policy.get("inherit_tools")

    if inherit is not None:
        filtered = [t for t in tools if t.get("module") in inherit]
    elif exclude:
        filtered = [t for t in tools if t.get("module") not in exclude]
    else:
        return config

    return {**config, "tools": filtered}


def _filter_hooks(
    config: dict[str, Any],
    policy: dict[str, list[str]],
) -> dict[str, Any]:
    """Filter hooks per inheritance policy (exclude list or allow list)."""
    hooks = config.get("hooks", [])
    if not hooks:
        return config

    exclude = policy.get("exclude_hooks", [])
    inherit = policy.get("inherit_hooks")

    if inherit is not None:
        filtered = [h for h in hooks if h.get("module") in inherit]
    elif exclude:
        filtered = [h for h in hooks if h.get("module") not in exclude]
    else:
        return config

    return {**config, "hooks": filtered}


def _apply_provider_override(
    config: dict[str, Any],
    provider_id: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Promote *provider_id* to priority 0, optionally setting *model*."""
    if not provider_id and not model:
        return config

    providers = config.get("providers", [])
    if not providers:
        return config

    # Locate target provider
    target_idx: int | None = None
    for i, p in enumerate(providers):
        mod = p.get("module", "")
        if provider_id and provider_id in (
            mod,
            mod.replace("provider-", ""),
            f"provider-{provider_id}",
        ):
            target_idx = i
            break

    # Model-only: apply to highest-priority (lowest number) provider
    if provider_id is None and model:
        min_pri: float = float("inf")
        for i, p in enumerate(providers):
            pri = p.get("config", {}).get("priority", 100)
            if pri < min_pri:
                min_pri, target_idx = pri, i

    if target_idx is None:
        logger.warning(
            "Provider '%s' not found in config; override skipped",
            provider_id,
        )
        return config

    new_providers = []
    for i, p in enumerate(providers):
        pc = dict(p)
        pc["config"] = dict(p.get("config", {}))
        if i == target_idx:
            pc["config"]["priority"] = 0
            if model:
                pc["config"]["model"] = model
        new_providers.append(pc)
    return {**config, "providers": new_providers}


# ---------------------------------------------------------------------------
# CLISpawnManager — in-process child session spawning for CLI mode
# ---------------------------------------------------------------------------


class CLISpawnManager:
    """Spawn manager for CLI mode — creates child ``AmplifierSession`` instances.

    Supports agent delegation (``session.spawn``) and session resume
    (``session.resume``) for recipes and sub-agent workflows.  Child
    sessions inherit the parent's configuration and capabilities, execute
    in-process, and return results.

    Parameters
    ----------
    prepared:
        The :class:`PreparedBundle` from the parent session, used as a
        fallback source for the module-source resolver.
    """

    def __init__(self, prepared: Any) -> None:
        self._prepared = prepared

    async def spawn(self, **kwargs: Any) -> dict[str, Any]:
        """Spawn a child session for agent delegation.

        Creates a child :class:`~amplifier_core.AmplifierSession`, merges
        the parent config with the agent overlay, inherits key capabilities
        (module resolver, mention resolver, ``sys.path`` entries), executes
        the instruction, and returns the result.

        Keyword Args
        ------------
        agent_name : str
            Agent name as it appears in *agent_configs*.
        instruction : str
            Task for the child agent to execute.
        parent_session : AmplifierSession
            Parent session for config / capability inheritance.
        agent_configs : dict[str, dict]
            Mapping of agent names to their configuration overlays.
        sub_session_id : str | None
            Explicit child session ID (auto-generated when ``None``).
        tool_inheritance : dict | None
            Tool filtering policy (``exclude_tools`` or ``inherit_tools``).
        hook_inheritance : dict | None
            Hook filtering policy (``exclude_hooks`` or ``inherit_hooks``).
        orchestrator_config : dict | None
            Orchestrator config overrides (e.g. rate limiting).
        parent_messages : list[dict] | None
            Parent context messages (reserved for future use).
        provider_override : str | None
            Provider ID to promote to priority 0.
        model_override : str | None
            Model name override for the promoted provider.

        Returns
        -------
        dict
            ``{"output": <response>, "session_id": <child_id>}``

        Raises
        ------
        ValueError
            If *agent_name* is not present in *agent_configs*.
        """
        from amplifier_core import AmplifierSession
        from amplifier_foundation import generate_sub_session_id

        # -- Unpack kwargs (matches tool-task calling convention) --
        agent_name: str = kwargs["agent_name"]
        instruction: str = kwargs["instruction"]
        parent_session = kwargs["parent_session"]
        agent_configs: dict[str, dict] = kwargs["agent_configs"]
        sub_session_id: str | None = kwargs.get("sub_session_id")
        tool_inheritance = kwargs.get("tool_inheritance")
        hook_inheritance = kwargs.get("hook_inheritance")
        orchestrator_config: dict | None = kwargs.get("orchestrator_config")
        provider_override: str | None = kwargs.get("provider_override")
        model_override: str | None = kwargs.get("model_override")

        # -- Resolve agent config --
        if agent_name not in agent_configs:
            raise ValueError(
                f"Agent '{agent_name}' not found in configuration"
            )
        agent_config = agent_configs[agent_name]

        # -- Merge parent config with agent overlay --
        merged_config = _merge_configs(parent_session.config, agent_config)

        if tool_inheritance and "tools" in merged_config:
            merged_config = _filter_tools(merged_config, tool_inheritance)
        if hook_inheritance and "hooks" in merged_config:
            merged_config = _filter_hooks(merged_config, hook_inheritance)
        if provider_override or model_override:
            merged_config = _apply_provider_override(
                merged_config, provider_override, model_override,
            )
        if orchestrator_config:
            session_cfg = merged_config.setdefault("session", {})
            orch_cfg = session_cfg.setdefault("orchestrator", {})
            orch_cfg.setdefault("config", {}).update(orchestrator_config)

        # -- Generate child session ID --
        if not sub_session_id:
            sub_session_id = generate_sub_session_id(
                agent_name=agent_name,
                parent_session_id=parent_session.session_id,
                parent_trace_id=getattr(parent_session, "trace_id", None),
            )

        # -- Create child session --
        from amplifier_app_openclaw.runner import AutoDenyApproval, StderrDisplay

        child_session = AmplifierSession(
            config=merged_config,
            session_id=sub_session_id,
            parent_id=parent_session.session_id,
            approval_system=AutoDenyApproval(),
            display_system=StderrDisplay(),
        )

        # -- Inherit capabilities BEFORE initialize() --

        # Module source resolver (needed for loading modules with source: directives)
        parent_resolver = parent_session.coordinator.get(
            "module-source-resolver",
        )
        if parent_resolver:
            await child_session.coordinator.mount(
                "module-source-resolver", parent_resolver,
            )
        elif hasattr(self._prepared, "resolver") and self._prepared.resolver:
            await child_session.coordinator.mount(
                "module-source-resolver", self._prepared.resolver,
            )

        # Share sys.path entries so bundle packages remain importable
        paths_to_share: list[str] = []
        if hasattr(parent_session, "loader") and parent_session.loader is not None:
            paths_to_share.extend(
                getattr(parent_session.loader, "_added_paths", []),
            )
        bundle_paths = parent_session.coordinator.get_capability(
            "bundle_package_paths",
        )
        if bundle_paths:
            paths_to_share.extend(bundle_paths)
        for p in paths_to_share:
            if p not in sys.path:
                sys.path.insert(0, p)

        # Initialize child (mounts modules per merged config)
        await child_session.initialize()

        # Mention resolver (inherited after init so context module is mounted)
        parent_mention = parent_session.coordinator.get_capability(
            "mention_resolver",
        )
        if parent_mention:
            child_session.coordinator.register_capability(
                "mention_resolver", parent_mention,
            )

        # Cancellation propagation: parent cancel → child cancel
        parent_cancel = getattr(
            parent_session.coordinator, "cancellation", None,
        )
        child_cancel = getattr(
            child_session.coordinator, "cancellation", None,
        )
        has_cancel = (
            parent_cancel is not None
            and child_cancel is not None
            and hasattr(parent_cancel, "register_child")
        )
        if has_cancel:
            parent_cancel.register_child(child_cancel)

        # -- Execute and clean up --
        try:
            response = await child_session.execute(instruction)
        finally:
            if has_cancel and hasattr(parent_cancel, "unregister_child"):
                parent_cancel.unregister_child(child_cancel)
            await child_session.cleanup()

        logger.debug(
            "Child session %s completed for agent '%s'",
            sub_session_id,
            agent_name,
        )
        return {"output": response, "session_id": sub_session_id}

    async def resume(self, **kwargs: Any) -> dict[str, Any]:
        """Resume a previously spawned child session.

        Session resume requires persisted transcript state which is not
        available in CLI mode.  Returns a graceful failure dict so callers
        (recipes, tool-task) can surface a user-friendly message instead of
        crashing.
        """
        return {
            "output": (
                "Session resume is not yet supported in CLI mode. "
                "Use 'amplifier-openclaw serve' for session persistence."
            ),
            "session_id": kwargs.get("sub_session_id", "unknown"),
            "status": "failed",
            "turn_count": 0,
            "metadata": {},
        }


# ---------------------------------------------------------------------------
# OpenClawSpawnManager — sidecar mode via PreparedBundle.spawn()
# ---------------------------------------------------------------------------


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
