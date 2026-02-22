"""Core session lifecycle — load bundle, execute prompt, return results."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


async def run_task(
    bundle_name: str,
    cwd: str,
    timeout: int,
    prompt: str,
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
    from amplifier_foundation import load_bundle
    from amplifier_foundation.mentions import BaseMentionResolver

    from amplifier_app_openclaw.spawn import CLISpawnManager

    session = None
    session_id = str(uuid.uuid4())
    start_time = time.monotonic()
    try:
        # Load and prepare bundle
        bundle = await load_bundle(bundle_name)
        bundle = CHAT_OVERLAY.compose(bundle)
        prepared = await bundle.prepare(install_deps=True)

        # Inject user-configured providers from ~/.amplifier/settings.yaml
        # (mirrors what amplifier-app-cli does via inject_user_providers)
        _inject_user_providers(prepared)

        # Create session with CLI-appropriate adapters
        session = await prepared.create_session(
            approval_system=AutoDenyApproval(),
            display_system=StderrDisplay(),
            session_cwd=Path(cwd),
        )

        # Register mention resolver
        resolver = BaseMentionResolver(base_path=Path(cwd))
        session.coordinator.register_capability("mention_resolver", resolver)

        # Register spawn/resume capabilities (stubs in Phase 0)
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
