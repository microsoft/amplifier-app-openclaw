"""Tests for the errors module."""

from __future__ import annotations

from unittest.mock import MagicMock

from amplifier_app_openclaw.errors import make_timeout_result, map_error


class TestMapError:
    def test_generic_fallback(self):
        r = map_error(RuntimeError("boom"))
        assert r["error"] == "boom"
        assert r["error_type"] == "RuntimeError"

    def test_keyboard_interrupt(self):
        r = map_error(KeyboardInterrupt())
        assert r["error_type"] == "KeyboardInterrupt"

    def test_module_load_error(self):
        exc = type("ModuleLoadError", (Exception,), {})("bad module")
        r = map_error(exc)
        assert "Failed to load module" in r["error"]
        assert r["error_type"] == "ModuleLoadError"

    def test_module_not_found(self):
        exc = ModuleNotFoundError("no such module")
        r = map_error(exc)
        assert "Module not found" in r["error"]

    def test_authentication_error(self):
        exc = type("AuthenticationError", (Exception,), {})("bad key")
        r = map_error(exc)
        assert "Authentication failed" in r["error"]

    def test_rate_limit_error(self):
        exc = type("RateLimitError", (Exception,), {})("slow down")
        exc.retry_after = 30
        r = map_error(exc)
        assert r["retryable"] is True
        assert r["retry_after"] == 30

    def test_bundle_not_found(self):
        exc = type("BundleNotFoundError", (Exception,), {})("no bundle")
        r = map_error(exc)
        assert "Bundle not found" in r["error"]

    def test_value_error_with_bundle(self):
        r = map_error(ValueError("Unknown bundle 'foo'"))
        assert "Bundle not found" in r["error"]

    def test_always_has_error_and_type(self):
        for exc in [RuntimeError("x"), TypeError(), OSError("disk")]:
            r = map_error(exc)
            assert "error" in r
            assert "error_type" in r


class TestMakeTimeoutResult:
    def test_basic(self):
        r = make_timeout_result()
        assert r["timed_out"] is True
        assert r["status"] == "timed_out"
        assert "[Task timed out]" in r["response"]

    def test_with_partial_response(self):
        r = make_timeout_result(partial_response="partial answer")
        assert r["response"] == "partial answer"

    def test_with_session(self):
        session = MagicMock()
        session.status.estimated_cost = 0.05
        session.status.total_input_tokens = 500
        session.status.total_output_tokens = 200
        session.status.tool_invocations = 1
        r = make_timeout_result(session=session)
        assert "usage" in r
        assert r["usage"]["estimated_cost"] == 0.05

    def test_with_session_none_cost(self):
        session = MagicMock()
        session.status.estimated_cost = None
        session.status.total_input_tokens = 0
        session.status.total_output_tokens = 0
        session.status.tool_invocations = 0
        r = make_timeout_result(session=session)
        assert r["usage"]["estimated_cost"] == 0.0
