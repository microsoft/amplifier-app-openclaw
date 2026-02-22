"""Handler for openclaw/llm_complete — bridges LLM calls from provider-openclaw.

When an Amplifier session uses provider-openclaw, LLM calls are routed via
JSON-RPC to this handler. For now, we call the Anthropic SDK directly using
the same API key from the environment. In future, this could proxy through
OpenClaw's actual provider layer.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-loaded Anthropic client
_client = None


def _get_client():
    """Get or create the Anthropic client."""
    global _client
    if _client is None:
        try:
            import anthropic
            _client = anthropic.AsyncAnthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
            )
        except ImportError:
            raise RuntimeError("anthropic package required for openclaw/llm_complete")
    return _client


async def handle_llm_complete(params: dict[str, Any]) -> dict[str, Any]:
    """Handle openclaw/llm_complete JSON-RPC method.

    Receives a serialized ChatRequest, calls the Anthropic API,
    and returns a serialized ChatResponse.
    """
    client = _get_client()

    # Extract messages and tools from the ChatRequest payload
    messages = params.get("messages", [])
    tools = params.get("tools")
    model = params.get("model") or os.environ.get("AMPLIFIER_DEFAULT_MODEL", "claude-sonnet-4-20250514")
    max_tokens = params.get("max_tokens", 8192)

    # Convert to Anthropic format
    anthropic_messages = []
    system_parts = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_parts.append(content if isinstance(content, str) else str(content))
            continue

        # Handle tool role → map to Anthropic's tool_result
        if role == "tool":
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else str(content),
                }],
            })
            continue

        # Handle assistant messages with tool_calls
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content if isinstance(content, str) else str(content)})
            for tc in tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc.get("tool", tc.get("name", "")),
                    "input": tc.get("arguments", {}),
                })
            anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        # Regular user/assistant message
        anthropic_messages.append({
            "role": role,
            "content": content if isinstance(content, str) else str(content),
        })

    # Build API call kwargs
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
    }

    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)

    # Convert tools to Anthropic format
    if tools:
        anthropic_tools = []
        for t in tools:
            tool_spec = {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", t.get("input_schema", {"type": "object"})),
            }
            anthropic_tools.append(tool_spec)
        kwargs["tools"] = anthropic_tools

    logger.info("openclaw/llm_complete: model=%s, messages=%d, tools=%d",
                model, len(anthropic_messages), len(tools or []))

    # Make the API call
    response = await client.messages.create(**kwargs)

    # Convert response to ChatResponse format
    content_blocks = []
    tool_calls = []
    text_parts = []

    for block in response.content:
        if block.type == "text":
            content_blocks.append({"type": "text", "text": block.text})
            text_parts.append(block.text)
        elif block.type == "tool_use":
            content_blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            })

    # Build usage info
    usage = {}
    if response.usage:
        usage = {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        }

    result = {
        "content": content_blocks,
        "text": "\n\n".join(text_parts),
        "tool_calls": tool_calls if tool_calls else None,
        "usage": usage,
        "model": response.model,
        "stop_reason": response.stop_reason,
    }

    logger.info("openclaw/llm_complete: response model=%s, usage=%s", response.model, usage)
    return result


async def handle_list_models(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle openclaw/list_models — return available models."""
    return [
        {
            "id": "claude-sonnet-4-20250514",
            "display_name": "Claude Sonnet 4",
            "context_window": 200000,
            "max_output_tokens": 64000,
            "capabilities": ["tools", "streaming"],
        },
        {
            "id": "claude-opus-4-6",
            "display_name": "Claude Opus 4",
            "context_window": 200000,
            "max_output_tokens": 32000,
            "capabilities": ["tools", "streaming"],
        },
    ]
