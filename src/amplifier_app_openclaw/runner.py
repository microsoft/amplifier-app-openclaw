"""Core session lifecycle — load bundle, execute prompt, return results."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


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
    try:
        # Load and prepare bundle
        bundle = await load_bundle(bundle_name)
        prepared = await bundle.prepare(install_deps=True)

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
            response = "[Task timed out]"

        # Collect status and usage
        cost = session.status.estimated_cost if session.status.estimated_cost is not None else 0.0
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
        return {"error": str(e), "error_type": type(e).__name__}

    finally:
        if session is not None:
            try:
                await session.cleanup()
            except Exception:
                pass
