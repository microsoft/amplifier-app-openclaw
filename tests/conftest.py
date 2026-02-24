"""Shared fixtures for amplifier-app-openclaw tests."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Mock amplifier-core and amplifier-foundation before any app imports
# ---------------------------------------------------------------------------

def _make_mock_module(name: str) -> ModuleType:
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod


# amplifier_core (top-level + submodules we might need)
if "amplifier_core" not in sys.modules:
    ac = _make_mock_module("amplifier_core")

    class _ToolResult:
        def __init__(self, success=True, output=None, error=None):
            self.success = success
            self.output = output
            self.error = error

    class _HookResult:
        def __init__(self, action="continue"):
            self.action = action

    ac.ToolResult = _ToolResult
    ac.AmplifierSession = MagicMock

    # Also need amplifier_core.models for HookResult
    acm = _make_mock_module("amplifier_core.models")
    acm.HookResult = _HookResult
else:
    ac = sys.modules["amplifier_core"]

# amplifier_foundation and its submodules
if "amplifier_core.hooks" not in sys.modules:
    ach = _make_mock_module("amplifier_core.hooks")

    class _HookResultHooks:
        def __init__(self, action="continue", data=None):
            self.action = action
            self.data = data
    ach.HookResult = _HookResultHooks

if "amplifier_foundation" not in sys.modules:
    af = _make_mock_module("amplifier_foundation")

    # Bundle class mock
    class _MockBundle:
        def __init__(self, *, name="mock", instruction="", **kw):
            self.name = name
            self.instruction = instruction
            for k, v in kw.items():
                setattr(self, k, v)

        def compose(self, other):
            return other

    af.Bundle = _MockBundle

    # load_bundle async mock
    af.load_bundle = AsyncMock()
    af.generate_sub_session_id = MagicMock(return_value="child-session-001")

if "amplifier_foundation.registry" not in sys.modules:
    reg = _make_mock_module("amplifier_foundation.registry")
    reg.BundleRegistry = MagicMock
    reg.load_bundle = AsyncMock()

if "amplifier_foundation.bundle" not in sys.modules:
    bundle_mod = _make_mock_module("amplifier_foundation.bundle")
    bundle_mod.PreparedBundle = MagicMock

if "amplifier_module_context_persistent" not in sys.modules:
    _make_mock_module("amplifier_module_context_persistent")

if "amplifier_module_context_simple" not in sys.modules:
    _make_mock_module("amplifier_module_context_simple")

if "amplifier_foundation.mentions" not in sys.modules:
    mentions = _make_mock_module("amplifier_foundation.mentions")
    mentions.BaseMentionResolver = MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeStatus:
    estimated_cost = 0.005
    total_input_tokens = 100
    total_output_tokens = 50
    tool_invocations = 2
    status = "completed"


class _FakeSession:
    status = _FakeStatus()

    async def execute(self, prompt: str) -> str:
        return f"Echo: {prompt}"

    async def cleanup(self) -> None:
        pass

    class coordinator:
        @staticmethod
        def register_capability(name, obj):
            pass


class _FakePrepared:
    async def create_session(self, **kw):
        return _FakeSession()


class _FakeBundle:
    name = "foundation"
    version = "1.0"

    def compose(self, other):
        return other

    async def prepare(self, **kw):
        return _FakePrepared()


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def fake_bundle():
    return _FakeBundle()


@pytest.fixture
def fake_prepared():
    return _FakePrepared()
