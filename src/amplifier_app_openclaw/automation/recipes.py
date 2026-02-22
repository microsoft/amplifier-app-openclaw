"""Automation Mode — Recipe execution and listing.

Provides ``recipe/execute`` and ``recipe/list`` JSON-RPC handlers.
Recipe execution creates a session with the *recipes* bundle, runs
the recipe name as a prompt, and returns the result with a
``deliver_to`` channel and cost summary.

See design spec §7 (Automation Mode).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from amplifier_app_openclaw.rpc import INVALID_PARAMS, JsonRpcWriter

logger = logging.getLogger(__name__)

# Default bundle used for recipe execution.
RECIPES_BUNDLE = "recipes"

# Default timeout for recipe execution (seconds).
DEFAULT_RECIPE_TIMEOUT = 300

# Static recipe catalogue.  In a future phase this can be populated
# dynamically from the recipes bundle's agent definitions.
_BUILTIN_RECIPES: list[dict[str, Any]] = [
    {
        "name": "daily-summary",
        "description": "Generate a daily summary of activity across channels.",
    },
    {
        "name": "weekly-report",
        "description": "Compile a weekly status report.",
    },
    {
        "name": "triage-inbox",
        "description": "Triage unread messages and flag urgent items.",
    },
]


async def handle_recipe_list(params: dict[str, Any]) -> Any:
    """``recipe/list`` — return available recipes.

    Params (all optional):
        - (none currently)

    Returns:
        ``{ recipes: [ { name, description }, ... ] }``
    """
    return {"recipes": list(_BUILTIN_RECIPES)}


async def handle_recipe_execute(
    params: dict[str, Any],
    *,
    writer: JsonRpcWriter | None = None,
) -> Any:
    """``recipe/execute`` — run a recipe by name.

    Params:
        - recipe_name (str, required): Name of the recipe to execute.
        - deliver_to (str, optional): Channel / destination for the result.
        - cwd (str, optional): Working directory for the session.
        - timeout (int, optional): Execution timeout in seconds.

    Returns a dict with:
        - response: str
        - deliver_to: str | None
        - cost_summary: dict
        - status: str
    """
    recipe_name = params.get("recipe_name")
    if not recipe_name:
        raise ValueError("recipe_name is required")

    deliver_to: str | None = params.get("deliver_to")
    cwd = params.get("cwd", str(Path.home()))
    timeout = params.get("timeout", DEFAULT_RECIPE_TIMEOUT)
    session_id = str(uuid.uuid4())

    prompt = f"Execute recipe: {recipe_name}"

    start_time = time.monotonic()

    try:
        from amplifier_foundation import load_bundle

        from amplifier_app_openclaw.adapters.approval import OpenClawApprovalSystem
        from amplifier_app_openclaw.runner import CHAT_OVERLAY, StderrDisplay

        bundle = await load_bundle(RECIPES_BUNDLE)
        bundle = CHAT_OVERLAY.compose(bundle)
        prepared = await bundle.prepare(install_deps=True)

        # Build approval system — if we have a JSON-RPC writer we route
        # approvals through OpenClaw; otherwise auto-deny.
        if writer is not None:
            approval_system: Any = OpenClawApprovalSystem(session_id, writer)
        else:
            from amplifier_app_openclaw.runner import AutoDenyApproval

            approval_system = AutoDenyApproval()

        session = await prepared.create_session(
            approval_system=approval_system,
            display_system=StderrDisplay(),
            session_cwd=Path(cwd),
        )

        try:
            response = await asyncio.wait_for(
                session.execute(prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await session.coordinator.request_cancel()
            duration = time.monotonic() - start_time
            return {
                "response": f"Recipe '{recipe_name}' timed out after {timeout}s.",
                "deliver_to": deliver_to,
                "cost_summary": _cost_summary(session, session_id, recipe_name, duration, prompt),
                "status": "timeout",
            }

        duration = time.monotonic() - start_time
        cost_summary = _cost_summary(session, session_id, recipe_name, duration, prompt)

        # Persist cost entry
        _log_cost(cost_summary)

        return {
            "response": response,
            "deliver_to": deliver_to,
            "cost_summary": cost_summary,
            "status": session.status.status if session.status else "completed",
        }

    except Exception as exc:
        duration = time.monotonic() - start_time
        logger.exception("Recipe execution failed: %s", recipe_name)
        return {
            "response": f"Recipe '{recipe_name}' failed: {exc}",
            "deliver_to": deliver_to,
            "cost_summary": {
                "session_id": session_id,
                "bundle": RECIPES_BUNDLE,
                "estimated_cost": 0.0,
                "duration_seconds": round(duration, 2),
            },
            "status": "error",
        }


def _cost_summary(
    session: Any,
    session_id: str,
    recipe_name: str,
    duration: float,
    prompt: str,
) -> dict[str, Any]:
    cost = 0.0
    input_tokens = 0
    output_tokens = 0
    tool_invocations = 0
    if session.status:
        cost = session.status.estimated_cost or 0.0
        input_tokens = session.status.total_input_tokens or 0
        output_tokens = session.status.total_output_tokens or 0
        tool_invocations = session.status.tool_invocations or 0

    return {
        "session_id": session_id,
        "bundle": RECIPES_BUNDLE,
        "recipe_name": recipe_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": cost,
        "tool_invocations": tool_invocations,
        "duration_seconds": round(duration, 2),
        "task_summary": prompt[:200],
    }


def _log_cost(summary: dict[str, Any]) -> None:
    """Persist a cost entry to the shared cost log."""
    try:
        from amplifier_app_openclaw.cost import CostEntry, log_cost_entry

        log_cost_entry(
            CostEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=summary["session_id"],
                bundle=summary["bundle"],
                input_tokens=summary.get("input_tokens", 0),
                output_tokens=summary.get("output_tokens", 0),
                estimated_cost=summary.get("estimated_cost", 0.0),
                duration_seconds=summary.get("duration_seconds", 0.0),
                task_summary=summary.get("task_summary", ""),
            )
        )
    except Exception:
        logger.debug("Failed to log cost entry", exc_info=True)


def register_recipe_handlers(
    rpc_reader: Any,
    writer: JsonRpcWriter | None = None,
) -> None:
    """Register ``recipe/list`` and ``recipe/execute`` on a JsonRpcReader."""

    rpc_reader.register("recipe/list", handle_recipe_list)

    async def _execute_wrapper(params: dict[str, Any]) -> Any:
        return await handle_recipe_execute(params, writer=writer)

    rpc_reader.register("recipe/execute", _execute_wrapper)
