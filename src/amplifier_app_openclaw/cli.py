"""Amplifier-OpenClaw CLI entry point."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import click

from amplifier_app_openclaw import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="amplifier-openclaw")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Amplifier × OpenClaw integration CLI."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option("--socket", "socket_path", default=None, help="Listen on a Unix socket instead of stdin/stdout.")
def serve(socket_path: str | None) -> None:
    """Start the JSON-RPC sidecar bridge.

    By default, communicates via stdin/stdout (for subprocess invocation).
    With --socket, listens on a Unix domain socket for persistent sidecar mode.
    """
    if socket_path is not None:
        from amplifier_app_openclaw.serve import run_serve_socket
        try:
            asyncio.run(run_serve_socket(socket_path if socket_path else None))
        except KeyboardInterrupt:
            pass
    else:
        from amplifier_app_openclaw.serve import run_serve
        try:
            asyncio.run(run_serve())
        except KeyboardInterrupt:
            pass


@cli.command()
@click.argument("prompt")
@click.option("--bundle", default="foundation", show_default=True, help="Bundle name to load.")
@click.option("--model", default=None, help="Model to use (e.g. anthropic/claude-opus-4-6). Auto-routes to best Amplifier provider.")
@click.option("--cwd", default=".", show_default=True, help="Working directory for the session.")
@click.option("--timeout", default=300, show_default=True, type=int, help="Timeout in seconds.")
@click.option("--persistent", is_flag=True, default=False, help="Enable session persistence (requires context-persistent module).")
@click.option("--session-name", default=None, help="Name for the session (enables deterministic session ID for later resumption).")
@click.option("--resume", "resume_session", is_flag=True, default=False, help="Resume a named session instead of creating a new one.")
def run(prompt: str, bundle: str, model: str | None, cwd: str, timeout: int, persistent: bool, session_name: str | None, resume_session: bool) -> None:
    """Run a single prompt through an Amplifier session.

    Outputs JSON to stdout with the session result.
    """
    os.environ["NO_COLOR"] = "1"

    if resume_session and not session_name:
        click.echo("Error: --resume requires --session-name", err=True)
        sys.exit(1)

    # If --session-name is provided, implicitly enable persistence
    if session_name:
        persistent = True

    from amplifier_app_openclaw.runner import run_task

    extra = ""
    if model:
        extra += f" model={model!r}"
    if persistent:
        extra += f" persistent=True"
    if session_name:
        extra += f" session_name={session_name!r}"
    if resume_session:
        extra += f" resume=True"
    print(f"[info] Running prompt with bundle={bundle!r} cwd={cwd!r} timeout={timeout}{extra}", file=sys.stderr)

    try:
        result = asyncio.run(run_task(
            bundle_name=bundle,
            cwd=cwd,
            timeout=timeout,
            prompt=prompt,
            model=model,
            persistent=persistent,
            session_name=session_name,
            resume=resume_session,
        ))
    except KeyboardInterrupt:
        result = {"error": "Cancelled by user", "error_type": "KeyboardInterrupt"}
        print("\n[info] Interrupted", file=sys.stderr)

    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("--period", default="day", type=click.Choice(["day", "week", "month", "all"]), show_default=True, help="Time period to report on.")
@click.option("--session", "session_id", default=None, help="Filter by session ID.")
def cost(period: str, session_id: str | None) -> None:
    """Show cost/usage report as JSON."""
    from amplifier_app_openclaw.cost import generate_cost_report

    report = generate_cost_report(period=period, session_id=session_id)
    click.echo(json.dumps(report, indent=2))


# ---------------------------------------------------------------------------
# bundles subgroup
# ---------------------------------------------------------------------------


@cli.group()
def bundles() -> None:
    """Manage Amplifier bundles (list, add)."""
    pass


@bundles.command(name="list")
@click.option("--root-only", is_flag=True, default=False, help="Show only root bundles (not sub-behaviors).")
def bundles_list(root_only: bool) -> None:
    """List registered bundles as JSON.

    Scans the Amplifier bundle registry for all known bundles
    (foundation, user-added, and cached). Outputs a JSON array
    with name, source, and status for each bundle.
    """
    from amplifier_foundation.registry import BundleRegistry

    registry = BundleRegistry()
    names = sorted(registry.list_registered())
    result = []
    for name in names:
        state = registry.get_state(name)
        if state is None:
            continue
        if root_only and not state.is_root:
            continue
        status = "cached" if state.local_path else "registered"
        if state.loaded_at is not None:
            status = "loaded"
        result.append({
            "name": name,
            "source": state.uri or "",
            "version": state.version,
            "status": status,
            "is_root": state.is_root,
        })
    click.echo(json.dumps(result, indent=2))


@bundles.command(name="add")
@click.argument("source")
def bundles_add(source: str) -> None:
    """Add a bundle from a git URI or local path.

    Loads the bundle via load_bundle() and runs prepare() to verify
    it works. Outputs JSON with the result.

    Examples:

        amplifier-openclaw bundles add git+https://github.com/org/repo@main

        amplifier-openclaw bundles add ./my-local-bundle
    """
    from amplifier_foundation.registry import BundleRegistry, load_bundle

    async def _add() -> dict:
        registry = BundleRegistry()
        bundle = await load_bundle(source, registry=registry)
        prepared = bundle.prepare()
        registry.save()
        return {
            "ok": True,
            "name": bundle.name,
            "version": getattr(bundle, "version", None),
            "source": source,
        }

    try:
        result = asyncio.run(_add())
    except Exception as e:
        result = {
            "ok": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "source": source,
        }
        click.echo(json.dumps(result, indent=2))
        sys.exit(1)

    click.echo(json.dumps(result, indent=2))
