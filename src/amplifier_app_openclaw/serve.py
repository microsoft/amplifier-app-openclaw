"""Bridge serve mode — JSON-RPC sidecar main loop.

Supports two modes:
- stdin/stdout (default): for direct subprocess invocation
- Unix socket (--socket): for persistent sidecar with multiple clients
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from amplifier_app_openclaw import __version__
from amplifier_app_openclaw.rpc import JsonRpcReader, JsonRpcResponseReader, JsonRpcWriter
from amplifier_app_openclaw.session_manager import SessionManager

logger = logging.getLogger(__name__)

SIDECAR_DIR = Path.home() / ".openclaw" / "amplifier"
DEFAULT_SOCKET = SIDECAR_DIR / "sidecar.sock"


async def _handle_shutdown(params: dict[str, Any]) -> Any:
    """Handle bridge/shutdown notification — raises SystemExit to stop the loop."""
    logger.info("Received bridge/shutdown")
    raise SystemExit(0)


def _setup_handlers(
    rpc_reader: JsonRpcReader,
    writer: JsonRpcWriter,
    response_reader: JsonRpcResponseReader,
) -> SessionManager:
    """Register all JSON-RPC handlers. Returns the session manager."""
    rpc_reader.register("bridge/shutdown", _handle_shutdown)

    session_manager = SessionManager(writer, response_reader)
    session_manager.register_handlers(rpc_reader)

    from amplifier_app_openclaw.automation.recipes import register_recipe_handlers
    register_recipe_handlers(rpc_reader, writer=writer)

    # NOTE: LLM passthrough (provider-openclaw, llm_handler) has been replaced
    # by provider routing + provider-litellm.  Model selection now happens at
    # session creation time via --model flag and provider_routing.py.

    return session_manager


async def run_serve() -> None:
    """Serve mode using stdin/stdout."""
    writer = JsonRpcWriter(sys.stdout)

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    rpc_reader = JsonRpcReader(reader, writer)
    response_reader = JsonRpcResponseReader(writer)
    rpc_reader.set_response_callback(response_reader.handle_response)

    session_manager = _setup_handlers(rpc_reader, writer, response_reader)

    writer.notify("bridge/ready", {"version": __version__, "pid": os.getpid()})

    try:
        await rpc_reader.run()
    except SystemExit:
        pass
    finally:
        logger.info("Shutting down — cleaning up active sessions")
        await session_manager.cleanup_all()

    logger.info("Serve loop exited")


async def run_serve_socket(socket_path: str | None = None) -> None:
    """Serve mode using a Unix domain socket for persistent sidecar.

    Each client connection gets its own reader/writer but shares the
    same SessionManager (and therefore the same PreparedBundle cache
    and active sessions).
    """
    sock_path = Path(socket_path) if socket_path else DEFAULT_SOCKET
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale socket
    if sock_path.exists():
        sock_path.unlink()

    # Shared state across all client connections
    shared_writer = None
    shared_response_reader = None
    session_manager = None

    async def handle_client(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        nonlocal shared_writer, shared_response_reader, session_manager

        logger.info("Client connected")

        # Create writer that writes to this client's stream
        class SocketWriter:
            """Adapts asyncio.StreamWriter to look like a file for JsonRpcWriter."""
            def write(self, data: str) -> int:
                client_writer.write(data.encode())
                return len(data)
            def flush(self) -> None:
                pass

        writer = JsonRpcWriter(SocketWriter())
        response_reader = JsonRpcResponseReader(writer)

        rpc_reader = JsonRpcReader(client_reader, writer)
        rpc_reader.set_response_callback(response_reader.handle_response)

        # Each connection gets its own handlers but shares session manager state
        if session_manager is None:
            session_manager = _setup_handlers(rpc_reader, writer, response_reader)
        else:
            # Re-register handlers for this connection's rpc_reader
            rpc_reader.register("bridge/shutdown", _handle_shutdown)
            session_manager.writer = writer
            session_manager.response_reader = response_reader
            session_manager.register_handlers(rpc_reader)
            from amplifier_app_openclaw.automation.recipes import register_recipe_handlers
            register_recipe_handlers(rpc_reader, writer=writer)

        # Send ready
        writer.notify("bridge/ready", {
            "version": __version__,
            "pid": os.getpid(),
            "mode": "socket",
        })

        try:
            await rpc_reader.run()
        except (SystemExit, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except Exception:
                pass
            logger.info("Client disconnected")

    # Write PID file
    pid_file = sock_path.parent / "sidecar.pid"
    pid_file.write_text(str(os.getpid()))

    server = await asyncio.start_unix_server(handle_client, path=str(sock_path))
    logger.info("Sidecar listening on %s (pid %d)", sock_path, os.getpid())

    # Print ready to stderr so the launcher knows we're up
    print(f'{{"status":"ready","socket":"{sock_path}","pid":{os.getpid()}}}', file=sys.stderr)

    try:
        async with server:
            await server.serve_forever()
    except (SystemExit, KeyboardInterrupt):
        pass
    finally:
        if session_manager:
            await session_manager.cleanup_all()
        pid_file.unlink(missing_ok=True)
        sock_path.unlink(missing_ok=True)
        logger.info("Socket serve exited")
