"""Tests for the provider-openclaw module."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_core import ModelInfo, ProviderInfo
from amplifier_core.message_models import (
    ChatRequest,
    ChatResponse,
    Message,
    TextBlock,
    ToolCall,
    ToolCallBlock,
    Usage,
)

from amplifier_app_openclaw.modules.provider_openclaw import OpenClawProvider, mount
from amplifier_app_openclaw.rpc_llm import deserialize_chat_response, serialize_chat_request


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_rpc():
    """A mock JsonRpcResponseReader."""
    rpc = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


@pytest.fixture
def provider(mock_rpc):
    return OpenClawProvider(mock_rpc, {"model": "claude-sonnet-4-20250514", "timeout": 60})


@pytest.fixture
def sample_request():
    return ChatRequest(
        messages=[Message(role="user", content="Hello")],
    )


@pytest.fixture
def sample_response_dict():
    return {
        "content": [{"type": "text", "text": "Hello back!"}],
        "tool_calls": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "finish_reason": "end_turn",
    }


# ---------------------------------------------------------------------------
# Provider basics
# ---------------------------------------------------------------------------

def test_provider_name(provider):
    assert provider.name == "openclaw"


def test_get_info_returns_provider_info(provider):
    info = provider.get_info()
    assert isinstance(info, ProviderInfo)
    assert info.id == "openclaw"
    assert info.display_name == "OpenClaw Gateway"
    assert info.defaults["model"] == "claude-sonnet-4-20250514"


async def test_list_models_fallback(provider, mock_rpc):
    """When RPC method is unavailable, list_models returns default model."""
    mock_rpc.request.side_effect = Exception("not implemented")
    models = await provider.list_models()
    assert len(models) == 1
    assert models[0].id == "claude-sonnet-4-20250514"


async def test_list_models_from_rpc(provider, mock_rpc):
    """When RPC returns model list, parse them."""
    mock_rpc.request.return_value = [
        {
            "id": "claude-sonnet-4-20250514",
            "display_name": "Claude Sonnet",
            "context_window": 200000,
            "max_output_tokens": 64000,
        }
    ]
    models = await provider.list_models()
    assert len(models) == 1
    assert isinstance(models[0], ModelInfo)
    assert models[0].id == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

async def test_complete_sends_and_receives(provider, mock_rpc, sample_request, sample_response_dict):
    mock_rpc.request.return_value = sample_response_dict

    response = await provider.complete(sample_request)

    assert isinstance(response, ChatResponse)
    assert response.content[0].text == "Hello back!"
    assert response.usage.total_tokens == 15

    # Verify RPC was called with correct method
    mock_rpc.request.assert_called_once()
    call_args = mock_rpc.request.call_args
    assert call_args[0][0] == "openclaw/llm_complete"


async def test_complete_timeout_override(provider, mock_rpc, sample_request, sample_response_dict):
    mock_rpc.request.return_value = sample_response_dict
    await provider.complete(sample_request, timeout=999)

    call_kwargs = mock_rpc.request.call_args[1]
    assert call_kwargs["timeout"] == 999


# ---------------------------------------------------------------------------
# parse_tool_calls()
# ---------------------------------------------------------------------------

def test_parse_tool_calls_empty(provider):
    response = ChatResponse(content=[TextBlock(text="hi")])
    assert provider.parse_tool_calls(response) == []


def test_parse_tool_calls_extracts(provider):
    tc = ToolCall(id="tc1", name="search", arguments={"query": "test"})
    response = ChatResponse(
        content=[TextBlock(text="")],
        tool_calls=[tc],
    )
    result = provider.parse_tool_calls(response)
    assert len(result) == 1
    assert result[0].name == "search"


def test_parse_tool_calls_filters_empty_args(provider):
    """Tool calls with empty arguments are valid (kept), only None would be filtered."""
    tc1 = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    tc2 = ToolCall(id="tc2", name="noop", arguments={})
    response = ChatResponse(
        content=[TextBlock(text="")],
        tool_calls=[tc1, tc2],
    )
    result = provider.parse_tool_calls(response)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def test_serialize_chat_request(sample_request):
    d = serialize_chat_request(sample_request)
    assert isinstance(d, dict)
    assert d["messages"][0]["role"] == "user"
    assert d["messages"][0]["content"] == "Hello"


def test_deserialize_chat_response(sample_response_dict):
    resp = deserialize_chat_response(sample_response_dict)
    assert isinstance(resp, ChatResponse)
    assert resp.content[0].text == "Hello back!"


# ---------------------------------------------------------------------------
# mount()
# ---------------------------------------------------------------------------

async def test_mount_registers_provider():
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=AsyncMock())
    coordinator.mount = AsyncMock()

    await mount(coordinator, {"model": "test-model"})

    coordinator.mount.assert_called_once()
    call_args = coordinator.mount.call_args
    assert call_args[0][0] == "providers"
    provider = call_args[0][1]
    assert provider.name == "openclaw"


async def test_mount_raises_without_rpc():
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=None)

    with pytest.raises(RuntimeError, match="openclaw.rpc_reader"):
        await mount(coordinator)
