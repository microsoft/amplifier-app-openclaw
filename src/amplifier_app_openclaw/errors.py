"""Map Amplifier exceptions to user-friendly JSON error dicts."""

from __future__ import annotations

from typing import Any


def map_error(exc: BaseException) -> dict[str, Any]:
    """Return a structured error dict for any exception.

    Each dict always contains at least ``error`` (str) and ``error_type`` (str).
    """
    etype = type(exc).__name__

    # --- Module loading / download failures ---
    if etype == "ModuleLoadError":
        return {
            "error": f"Failed to load module: {exc}. Check your internet connection and try again.",
            "error_type": etype,
        }

    # --- Module not found (import / resolution) ---
    if etype == "ModuleNotFoundError":
        return {
            "error": f"Module not found: {exc}. Verify the bundle configuration references valid modules.",
            "error_type": etype,
        }

    # --- Authentication / API key errors ---
    if etype == "AuthenticationError":
        return {
            "error": f"Authentication failed: {exc}. Check that your API key is set correctly.",
            "error_type": etype,
        }

    # --- Rate limit ---
    if etype == "RateLimitError":
        retry_after = getattr(exc, "retry_after", None)
        return {
            "error": f"Rate limited by the provider: {exc}",
            "error_type": etype,
            "retryable": True,
            "retry_after": retry_after,
        }

    # --- Bundle not found ---
    if etype in ("BundleNotFoundError", "BundleError") or (
        etype == "ValueError" and "bundle" in str(exc).lower()
    ):
        available = _list_bundles_safe()
        result: dict[str, Any] = {
            "error": f"Bundle not found: {exc}.",
            "error_type": etype,
        }
        if available is not None:
            result["available_bundles"] = available
            result["error"] += f" Available bundles: {', '.join(available)}"
        return result

    # --- Keyboard interrupt (already handled in cli.py, but just in case) ---
    if isinstance(exc, KeyboardInterrupt):
        return {"error": "Cancelled by user", "error_type": "KeyboardInterrupt"}

    # --- Generic fallback ---
    return {
        "error": str(exc) or etype,
        "error_type": etype,
    }


def make_timeout_result(
    partial_response: str | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Build a result dict for a timed-out session.

    If *session* is provided its status/usage info is included.
    """
    result: dict[str, Any] = {
        "response": partial_response or "[Task timed out]",
        "status": "timed_out",
        "timed_out": True,
    }
    if session is not None and hasattr(session, "status"):
        cost = session.status.estimated_cost if session.status.estimated_cost is not None else 0.0
        result["usage"] = {
            "input_tokens": session.status.total_input_tokens,
            "output_tokens": session.status.total_output_tokens,
            "estimated_cost": cost,
            "tool_invocations": session.status.tool_invocations,
        }
    return result


def _list_bundles_safe() -> list[str] | None:
    """Best-effort listing of registered bundle names."""
    try:
        from amplifier_foundation.registry import BundleRegistry

        registry = BundleRegistry()
        return sorted(registry.list_registered())
    except Exception:
        return None
