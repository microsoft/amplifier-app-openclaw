"""LLM request/response serialization helpers for JSON-RPC transport."""

from __future__ import annotations

from typing import Any

from amplifier_core.message_models import ChatRequest, ChatResponse


def serialize_chat_request(request: ChatRequest) -> dict[str, Any]:
    """Serialize a ChatRequest to a JSON-safe dict for RPC transport."""
    return request.model_dump(mode="json", exclude_none=True)


def deserialize_chat_response(data: dict[str, Any]) -> ChatResponse:
    """Deserialize a dict from RPC transport back to a ChatResponse."""
    return ChatResponse.model_validate(data)
