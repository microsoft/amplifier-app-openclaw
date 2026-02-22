"""JSON-RPC 2.0 protocol layer for stdin/stdout communication."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Application error codes
SESSION_ERROR = -1
BUNDLE_ERROR = -2
TIMEOUT_ERROR = -3
CANCELLED_ERROR = -4


class JsonRpcWriter:
    """Writes JSON-RPC 2.0 messages to a stream (newline-delimited)."""

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream or sys.stdout
        self._next_id = 1
        self._lock = asyncio.Lock()

    def _write(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, separators=(",", ":"))
        self._stream.write(line + "\n")
        self._stream.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> int:
        """Write a JSON-RPC request. Returns the assigned id."""
        rid = self._next_id
        self._next_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        return rid

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Write a JSON-RPC notification (no id)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def respond(self, rid: int | str, result: Any) -> None:
        """Write a JSON-RPC success response."""
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    def error(
        self,
        rid: int | str | None,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        """Write a JSON-RPC error response."""
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._write({"jsonrpc": "2.0", "id": rid, "error": err})


# Type for method handlers: async (params) -> result
MethodHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]


class JsonRpcReader:
    """Reads JSON-RPC messages from an async stream and dispatches to handlers."""

    def __init__(self, reader: asyncio.StreamReader, writer: JsonRpcWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._handlers: dict[str, MethodHandler] = {}

    def register(self, method: str, handler: MethodHandler) -> None:
        """Register an async handler for a JSON-RPC method."""
        self._handlers[method] = handler

    async def run(self) -> None:
        """Read lines and dispatch until EOF."""
        while True:
            line = await self._reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON-RPC message, skipping: %s", line[:200])
                continue

            if not isinstance(msg, dict):
                logger.warning("JSON-RPC message is not an object, skipping")
                continue

            # Dispatch based on message type
            if "method" in msg:
                await self._handle_request_or_notification(msg)
            elif "result" in msg or "error" in msg:
                # Response — handled by JsonRpcResponseReader via on_response callback
                if self._on_response:
                    self._on_response(msg)
            else:
                logger.warning("Unrecognized JSON-RPC message: %s", msg)

    _on_response: Callable[[dict[str, Any]], None] | None = None

    def set_response_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Set callback for incoming response messages."""
        self._on_response = cb

    async def _handle_request_or_notification(self, msg: dict[str, Any]) -> None:
        method = msg["method"]
        params = msg.get("params", {})
        rid = msg.get("id")  # None for notifications

        handler = self._handlers.get(method)
        if handler is None:
            if rid is not None:
                self._writer.error(rid, METHOD_NOT_FOUND, f"Method not found: {method}")
            else:
                logger.warning("No handler for notification: %s", method)
            return

        try:
            result = await handler(params)
            if rid is not None:
                self._writer.respond(rid, result)
        except Exception as exc:
            logger.exception("Handler error for %s", method)
            if rid is not None:
                self._writer.error(rid, SESSION_ERROR, str(exc))


class JsonRpcResponseReader:
    """Correlates JSON-RPC responses to pending requests using asyncio.Future."""

    def __init__(self, writer: JsonRpcWriter, default_timeout: float = 60.0) -> None:
        self._writer = writer
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._default_timeout = default_timeout

    def handle_response(self, msg: dict[str, Any]) -> None:
        """Feed a response message for correlation. Use as the response callback."""
        rid = msg.get("id")
        if rid is None:
            return
        future = self._pending.pop(rid, None)
        if future is None:
            logger.warning("Response for unknown id: %s", rid)
            return
        if "error" in msg:
            future.set_exception(JsonRpcError(msg["error"]))
        else:
            future.set_result(msg.get("result"))

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a request and await the correlated response."""
        rid = self._writer.request(method, params)
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout or self._default_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise JsonRpcError({"code": TIMEOUT_ERROR, "message": f"Timeout waiting for response to {method}"})


class JsonRpcError(Exception):
    """Raised when a JSON-RPC error response is received."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.code: int = error.get("code", -1)
        self.rpc_message: str = error.get("message", "Unknown error")
        self.data: Any = error.get("data")
        super().__init__(f"JSON-RPC error {self.code}: {self.rpc_message}")
