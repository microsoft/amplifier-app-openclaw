"""Core session lifecycle — load bundle, execute prompt, return results."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from amplifier_foundation import Bundle

# Chat overlay: composed onto every bundle so the agent knows it's in a
# messaging context.  Because compose() uses "later overrides earlier" for
# instruction, we put the overlay *first* so the bundle's own instruction
# takes precedence.
CHAT_OVERLAY = Bundle(
    name="_chat_overlay",
    instruction=(
        "You are assisting a user through a messaging interface "
        "(WhatsApp, Telegram, Discord, etc.). Keep responses concise "
        "and focused. Avoid verbose explanations unless specifically "
        "asked. Use short paragraphs. Skip unnecessary preamble."
    ),
)


class AutoDenyApproval:
    """Non-interactive approval system: auto-denies all requests.

    In CLI mode there is no user to approve anything, so we return the
    default option (typically "deny") immediately.
    """

    async def request_approval(
        self,
        prompt: str,
        options: list[str],
        timeout: float = 300.0,
        default: str = "deny",
    ) -> str:
        return default


class StderrDisplay:
    """Writes display messages to stderr (stdout is reserved for JSON)."""

    def show_message(
        self,
        message: str,
        level: str = "info",
        source: str = "hook",
    ) -> None:
        print(f"[{level}] {message}", file=sys.stderr)


# Well-known bundles and their git sources — mirrors amplifier-app-cli's discovery.
_WELL_KNOWN_BUNDLES: dict[str, str] = {
    "foundation": "git+https://github.com/microsoft/amplifier-foundation@main",
    "superpowers": "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/superpowers-methodology.yaml",
}


async def _ensure_bundle_registered(bundle_name: str) -> None:
    """No-op — bootstrap is handled in run_task via registry pass-through."""
    pass


async def run_task(
    bundle_name: str,
    cwd: str,
    timeout: int,
    prompt: str,
    model: str | None = None,
    persistent: bool = False,
    session_name: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Execute a single Amplifier task and return structured results.

    Returns a dict with keys:
      - response: str — the agent's response text
      - usage: dict — token counts and cost
      - status: str — session status ("completed", "cancelled", etc.)

    On failure returns:
      - error: str — error message
      - error_type: str — exception class name
    """
    import os

    from amplifier_foundation import load_bundle
    from amplifier_foundation.mentions import BaseMentionResolver

    from amplifier_app_openclaw.spawn import CLISpawnManager

    import copy
    from amplifier_app_openclaw.session_manager import (
        _deterministic_session_id,
        _context_persistent_available,
        _PERSISTENT_SESSIONS_DIR,
    )

    session = None
    is_resumed = False

    # Determine session ID
    if session_name:
        session_id = _deterministic_session_id(bundle_name, session_name)
    else:
        session_id = str(uuid.uuid4())

    start_time = time.monotonic()
    try:
        # Load bundle — try by name first, fall back to well-known git source
        try:
            bundle = await load_bundle(bundle_name)
        except Exception:
            source = _WELL_KNOWN_BUNDLES.get(bundle_name)
            if source:
                logger.info("Bundle '%s' not in registry, loading from %s", bundle_name, source)
                bundle = await load_bundle(source)
            else:
                raise
        bundle = CHAT_OVERLAY.compose(bundle)

        # Build provider overlay from OpenClaw config.
        # This reads auth-profiles.json for credentials and routes the model
        # to the appropriate Amplifier provider module.  The overlay is composed
        # onto the bundle BEFORE prepare(), so credentials flow through
        # Amplifier's normal composition → prepare → mount plan pipeline.
        from amplifier_app_openclaw.openclaw_config import build_openclaw_provider_overlay
        provider_config = build_openclaw_provider_overlay(model)
        if provider_config:
            provider_overlay = Bundle(
                name="_openclaw_provider",
                providers=[provider_config],
            )
            bundle = bundle.compose(provider_overlay)
            logger.info(
                "Composed OpenClaw provider overlay: %s (model: %s)",
                provider_config.get("module"), model or "default",
            )
        else:
            # No OpenClaw config available — fall back to user providers
            # (these will be injected post-prepare via settings.yaml)
            logger.info("No OpenClaw provider config; falling back to settings.yaml")

        prepared = await bundle.prepare(install_deps=True)

        if provider_config:
            # REPLACE all providers with just the routed one.
            # Bundle.compose() merges providers by module ID, so the base bundle's
            # providers (e.g. provider-anthropic from foundation) survive the merge.
            # Since we're running under OpenClaw with a specific model/provider,
            # we want ONLY our routed provider — not whatever the bundle shipped with
            # or what env vars happen to make available.
            prepared.mount_plan["providers"] = [provider_config]
            logger.info("Replaced mount plan providers with OpenClaw-routed provider only")
        else:
            # No OpenClaw config — try settings.yaml fallback
            _inject_user_providers(prepared)

        # Handle persistence
        if persistent and not _context_persistent_available():
            logger.warning("context-persistent module not available; falling back to non-persistent")
            persistent = False

        session_dir = _PERSISTENT_SESSIONS_DIR / session_id
        transcript_path = session_dir / "context-messages.jsonl"

        if persistent and (resume or transcript_path.exists()):
            is_resumed = True
            if resume and not transcript_path.exists():
                raise RuntimeError(f"No saved session found for session name: {session_name}")

        if persistent:
            # Deep-copy mount_plan to avoid mutating the cached PreparedBundle
            mount_plan = copy.deepcopy(prepared.mount_plan)
            session_section = mount_plan.get("session", {})

            # Resolve installed path for context-persistent so the module
            # loader can find it via lazy activation
            context_persistent_source = None
            try:
                import amplifier_module_context_persistent
                mod_file = getattr(amplifier_module_context_persistent, "__file__", None)
                if mod_file:
                    context_persistent_source = os.path.dirname(mod_file)
            except ImportError:
                pass

            context_config = {
                "module": "context-persistent",
                "config": {
                    "transcript_path": str(transcript_path),
                    "max_tokens": 200000,
                },
            }
            if context_persistent_source:
                context_config["source"] = context_persistent_source

            session_section["context"] = context_config
            mount_plan["session"] = session_section

            from amplifier_core import AmplifierSession
            session = AmplifierSession(
                mount_plan,
                session_id=session_id,
                approval_system=AutoDenyApproval(),
                display_system=StderrDisplay(),
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
                approval_system=AutoDenyApproval(),
                display_system=StderrDisplay(),
                session_cwd=Path(cwd),
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

        # Register mention resolver
        resolver = BaseMentionResolver(base_path=Path(cwd))
        session.coordinator.register_capability("mention_resolver", resolver)

        # Register spawn/resume capabilities for agent delegation and recipes
        spawn_mgr = CLISpawnManager(prepared)
        session.coordinator.register_capability("session.spawn", spawn_mgr.spawn)
        session.coordinator.register_capability("session.resume", spawn_mgr.resume)

        # Execute with timeout
        try:
            response = await asyncio.wait_for(
                session.execute(prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await session.coordinator.request_cancel()
            from amplifier_app_openclaw.errors import make_timeout_result

            return make_timeout_result(session=session)

        # Collect status and usage; log cost
        cost = session.status.estimated_cost if session.status.estimated_cost is not None else 0.0
        duration = time.monotonic() - start_time

        try:
            from amplifier_app_openclaw.cost import CostEntry, log_cost_entry

            log_cost_entry(CostEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=session_id,
                bundle=bundle_name,
                input_tokens=session.status.total_input_tokens or 0,
                output_tokens=session.status.total_output_tokens or 0,
                estimated_cost=cost,
                duration_seconds=round(duration, 2),
                task_summary=prompt[:200],
            ))
        except Exception:
            pass  # Don't fail the run if cost logging fails

        return {
            "response": response,
            "usage": {
                "input_tokens": session.status.total_input_tokens,
                "output_tokens": session.status.total_output_tokens,
                "estimated_cost": cost,
                "tool_invocations": session.status.tool_invocations,
            },
            "status": session.status.status,
        }

    except Exception as e:
        logger.exception("Task execution failed")
        from amplifier_app_openclaw.errors import map_error

        return map_error(e)

    finally:
        if session is not None:
            try:
                await session.cleanup()
            except Exception:
                pass


def _inject_user_providers(prepared: Any) -> None:
    """Read provider config from ~/.amplifier/settings.yaml and inject into bundle.

    This mirrors amplifier-app-cli's inject_user_providers() — the app layer
    provides the provider policy while the bundle provides the mechanism.
    Only injects if the bundle has no providers already defined.
    """
    import os
    import re

    try:
        import yaml
    except ImportError:
        return  # pyyaml not available, skip

    settings_path = Path.home() / ".amplifier" / "settings.yaml"
    if not settings_path.exists():
        return

    try:
        with open(settings_path) as f:
            settings = yaml.safe_load(f) or {}
    except Exception:
        return

    providers_config = settings.get("config", {}).get("providers", [])
    if not providers_config:
        return

    # Only inject if bundle has no providers
    if prepared.mount_plan.get("providers"):
        return

    # Resolve environment variables in provider config
    resolved = []
    for provider in providers_config:
        resolved_provider = _resolve_env_vars(provider)
        resolved.append(resolved_provider)

    prepared.mount_plan["providers"] = resolved


def _resolve_env_vars(obj: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    import os
    import re

    if isinstance(obj, str):
        def _replacer(m: re.Match) -> str:
            # Return env value if set, empty string if not (don't keep ${...} literal)
            return os.environ.get(m.group(1), "")
        resolved = re.sub(r"\$\{(\w+)\}", _replacer, obj)
        # If the entire value was just an unset env var, return None
        # so the provider SDK uses its default
        return resolved if resolved else None
    elif isinstance(obj, dict):
        return {k: v for k, v in ((k, _resolve_env_vars(v)) for k, v in obj.items()) if v is not None}
    elif isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj
