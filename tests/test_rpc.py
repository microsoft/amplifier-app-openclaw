"""Tests for JSON-RPC protocol layer (rpc.py)."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from amplifier_app_openclaw.rpc import (
    JsonRpcError,
    JsonRpcReader,
    JsonRpcResponseReader,
    JsonRpcWriter,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SESSION_ERROR,
    TIMEOUT_ERROR,
)


# ---------------------------------------------------------------------------
# JsonRpcWriter tests
# ---------------------------------------------------------------------------

class TestJsonRpcWriter:
    def setup_method(self):
        self.buf = io.StringIO()
        self.writer = JsonRpcWriter(self.buf)

    def _last_msg(self):
        return json.loads(self.buf.getvalue().strip().split("\n")[-1])

    def test_request_format(self):
        rid = self.writer.request("session/create", {"bundle": "test"})
        msg = self._last_msg()
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == rid
        assert msg["method"] == "session/create"
        assert msg["params"] == {"bundle": "test"}

    def test_request_no_params(self):
        self.writer.request("ping")
        msg = self._last_msg()
        assert "params" not in msg
        assert msg["method"] == "ping"

    def test_request_increments_id(self):
        r1 = self.writer.request("a")
        r2 = self.writer.request("b")
        assert r2 == r1 + 1

    def test_notify_no_id(self):
        self.writer.notify("session/event", {"type": "delta"})
        msg = self._last_msg()
        assert "id" not in msg
        assert msg["method"] == "session/event"

    def test_notify_no_params(self):
        self.writer.notify("ping")
        msg = self._last_msg()
        assert "params" not in msg

    def test_respond_format(self):
        self.writer.respond(42, {"ok": True})
        msg = self._last_msg()
        assert msg == {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}

    def test_error_format(self):
        self.writer.error(7, -32600, "Invalid request")
        msg = self._last_msg()
        assert msg["id"] == 7
        assert msg["error"]["code"] == -32600
        assert msg["error"]["message"] == "Invalid request"
        assert "data" not in msg["error"]

    def test_error_with_data(self):
        self.writer.error(1, -1, "fail", data={"detail": "x"})
        msg = self._last_msg()
        assert msg["error"]["data"] == {"detail": "x"}

    def test_error_null_id(self):
        self.writer.error(None, PARSE_ERROR, "Parse error")
        msg = self._last_msg()
        assert msg["id"] is None

    def test_newline_delimited(self):
        self.writer.request("a")
        self.writer.request("b")
        lines = self.buf.getvalue().strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# JsonRpcReader tests
# ---------------------------------------------------------------------------

class TestJsonRpcReader:
    @pytest.mark.asyncio
    async def test_dispatch_request(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        handler_called = {}

        async def handler(params):
            handler_called.update(params)
            return {"status": "ok"}

        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {"key": "val"}}) + "\n"
        stream = asyncio.StreamReader()
        stream.feed_data(msg.encode())
        stream.feed_eof()

        reader = JsonRpcReader(stream, writer)
        reader.register("test/method", handler)
        await reader.run()

        assert handler_called == {"key": "val"}
        # Should have written a response
        resp = json.loads(buf.getvalue().strip())
        assert resp["result"] == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)

        msg = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "unknown/thing"}) + "\n"
        stream = asyncio.StreamReader()
        stream.feed_data(msg.encode())
        stream.feed_eof()

        reader = JsonRpcReader(stream, writer)
        await reader.run()

        resp = json.loads(buf.getvalue().strip())
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_notification_no_response(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        called = False

        async def handler(params):
            nonlocal called
            called = True

        msg = json.dumps({"jsonrpc": "2.0", "method": "notify/test"}) + "\n"
        stream = asyncio.StreamReader()
        stream.feed_data(msg.encode())
        stream.feed_eof()

        reader = JsonRpcReader(stream, writer)
        reader.register("notify/test", handler)
        await reader.run()

        assert called
        assert buf.getvalue() == ""  # No response for notifications

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)

        data = b"not json\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n"
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()

        async def handler(params):
            return "pong"

        reader = JsonRpcReader(stream, writer)
        reader.register("ping", handler)
        await reader.run()

        # ping should still be handled despite malformed first line
        resp = json.loads(buf.getvalue().strip())
        assert resp["result"] == "pong"

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)

        async def bad_handler(params):
            raise ValueError("boom")

        msg = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "fail"}) + "\n"
        stream = asyncio.StreamReader()
        stream.feed_data(msg.encode())
        stream.feed_eof()

        reader = JsonRpcReader(stream, writer)
        reader.register("fail", bad_handler)
        await reader.run()

        resp = json.loads(buf.getvalue().strip())
        assert resp["error"]["code"] == SESSION_ERROR
        assert "boom" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_response_callback(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        received = []

        msg = json.dumps({"jsonrpc": "2.0", "id": 10, "result": "hello"}) + "\n"
        stream = asyncio.StreamReader()
        stream.feed_data(msg.encode())
        stream.feed_eof()

        reader = JsonRpcReader(stream, writer)
        reader.set_response_callback(lambda m: received.append(m))
        await reader.run()

        assert len(received) == 1
        assert received[0]["result"] == "hello"


# ---------------------------------------------------------------------------
# JsonRpcResponseReader tests
# ---------------------------------------------------------------------------

class TestJsonRpcResponseReader:
    @pytest.mark.asyncio
    async def test_request_and_correlate(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rr = JsonRpcResponseReader(writer)

        task = asyncio.create_task(rr.request("test/method", {"a": 1}))
        await asyncio.sleep(0.01)

        # Parse the written request to get the id
        sent = json.loads(buf.getvalue().strip())
        rr.handle_response({"jsonrpc": "2.0", "id": sent["id"], "result": "ok"})

        result = await task
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_error_response_raises(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rr = JsonRpcResponseReader(writer)

        task = asyncio.create_task(rr.request("fail", timeout=5))
        await asyncio.sleep(0.01)

        sent = json.loads(buf.getvalue().strip())
        rr.handle_response({"jsonrpc": "2.0", "id": sent["id"], "error": {"code": -1, "message": "nope"}})

        with pytest.raises(JsonRpcError) as exc_info:
            await task
        assert exc_info.value.code == -1

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rr = JsonRpcResponseReader(writer, default_timeout=0.05)

        with pytest.raises(JsonRpcError) as exc_info:
            await rr.request("slow")
        assert exc_info.value.code == TIMEOUT_ERROR

    @pytest.mark.asyncio
    async def test_out_of_order_responses(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rr = JsonRpcResponseReader(writer)

        t1 = asyncio.create_task(rr.request("a", timeout=5))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(rr.request("b", timeout=5))
        await asyncio.sleep(0.01)

        lines = buf.getvalue().strip().split("\n")
        msg1 = json.loads(lines[0])
        msg2 = json.loads(lines[1])

        # Respond to second first
        rr.handle_response({"jsonrpc": "2.0", "id": msg2["id"], "result": "B"})
        rr.handle_response({"jsonrpc": "2.0", "id": msg1["id"], "result": "A"})

        assert await t1 == "A"
        assert await t2 == "B"

    def test_unknown_response_id_ignored(self):
        buf = io.StringIO()
        writer = JsonRpcWriter(buf)
        rr = JsonRpcResponseReader(writer)
        # Should not raise
        rr.handle_response({"jsonrpc": "2.0", "id": 9999, "result": "orphan"})
