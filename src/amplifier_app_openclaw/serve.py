"""Bridge serve mode — JSON-RPC sidecar main loop."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from amplifier_app_openclaw import __version__
from amplifier_app_openclaw.rpc import JsonRpcReader, JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.session_manager import SessionManager

logger = logging.getLogger(__name__)


async def _handle_shutdown(params: dict[str, Any]) -> Any:
    """Handle bridge/shutdown notification — raises SystemExit to stop the loop."""
    logger.info("Received bridge/shutdown")
    raise SystemExit(0)


async def run_serve() -> None:
    """Main async entry point for serve mode."""
    writer = JsonRpcWriter(sys.stdout)

    # Create async reader for stdin
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    rpc_reader = JsonRpcReader(reader, writer)

    # Create response reader for outbound request correlation
    response_reader = JsonRpcResponseReader(writer)
    rpc_reader.set_response_callback(response_reader.handle_response)

    # Register built-in handlers
    rpc_reader.register("bridge/shutdown", _handle_shutdown)

    # Create session manager and register all session/bundle/augment handlers
    session_manager = SessionManager(writer, response_reader)
    session_manager.register_handlers(rpc_reader)

    # Recipe / automation handlers
    from amplifier_app_openclaw.automation.recipes import register_recipe_handlers

    register_recipe_handlers(rpc_reader, writer=writer)

    # Emit bridge/ready
    writer.notify("bridge/ready", {"version": __version__, "pid": os.getpid()})

    # Read and dispatch until EOF or shutdown
    try:
        await rpc_reader.run()
    except SystemExit:
        pass
    finally:
        # Clean shutdown: clean up all active sessions
        logger.info("Shutting down — cleaning up active sessions")
        await session_manager.cleanup_all()

    logger.info("Serve loop exited")
