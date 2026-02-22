"""Provider module that routes LLM calls through OpenClaw's gateway.

This provider sends ChatRequest objects over JSON-RPC to OpenClaw, which
performs the actual LLM call using its configured provider/model/API keys
and returns the response.  Amplifier sees a standard Provider interface.
"""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core import ModelInfo, ProviderInfo
from amplifier_core.message_models import ChatRequest, ChatResponse, ToolCall

from amplifier_app_openclaw.rpc import JsonRpcResponseReader
from amplifier_app_openclaw.rpc_llm import deserialize_chat_response, serialize_chat_request

logger = logging.getLogger(__name__)

__amplifier_module_type__ = "provider"

# Default timeout for LLM calls (seconds) — generous for long completions
_DEFAULT_LLM_TIMEOUT = 300.0


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the OpenClaw provider.

    Args:
        coordinator: Amplifier ModuleCoordinator.
        config: Optional provider config (model, timeout, etc.).
    """
    config = config or {}

    rpc_reader = coordinator.get_capability("openclaw.rpc_reader")
    if rpc_reader is None:
        raise RuntimeError(
            "openclaw.rpc_reader capability not registered — "
            "provider-openclaw requires an active OpenClaw sidecar connection"
        )

    provider = OpenClawProvider(rpc_reader, config)
    await coordinator.mount("providers", provider, name="openclaw")
    logger.info("Mounted OpenClawProvider (model=%s)", provider.default_model)


class OpenClawProvider:
    """Amplifier provider that delegates LLM calls to OpenClaw via JSON-RPC.

    OpenClaw owns the provider credentials and model selection.  This thin
    proxy serializes ``ChatRequest`` → JSON-RPC ``openclaw/llm_complete`` →
    deserializes ``ChatResponse``.
    """

    name = "openclaw"

    def __init__(
        self,
        rpc_reader: JsonRpcResponseReader,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._rpc = rpc_reader
        self.config = config or {}
        self.default_model: str = self.config.get("model", "default")
        self._timeout: float = float(self.config.get("timeout", _DEFAULT_LLM_TIMEOUT))

    # ------------------------------------------------------------------
    # Provider protocol
    # ------------------------------------------------------------------

    def get_info(self) -> ProviderInfo:
        """Return provider metadata."""
        return ProviderInfo(
            id="openclaw",
            display_name="OpenClaw Gateway",
            credential_env_vars=[],  # OpenClaw manages credentials
            capabilities=["tools", "streaming"],
            defaults={
                "model": self.default_model,
                "timeout": self._timeout,
            },
        )

    async def list_models(self) -> list[ModelInfo]:
        """Query OpenClaw for available models.

        Falls back to returning the configured default model if the RPC
        method is not implemented on the OpenClaw side.
        """
        try:
            result = await self._rpc.request("openclaw/list_models", timeout=10.0)
            if isinstance(result, list):
                return [
                    ModelInfo.model_validate(m) if isinstance(m, dict) else m
                    for m in result
                ]
        except Exception:
            logger.debug("openclaw/list_models not available, returning default")

        # Fallback: advertise the configured model
        return [
            ModelInfo(
                id=self.default_model,
                display_name=self.default_model,
                context_window=200_000,
                max_output_tokens=64_000,
                capabilities=["tools", "streaming"],
            )
        ]

    async def complete(self, request: ChatRequest, **kwargs: Any) -> ChatResponse:
        """Send a completion request to OpenClaw and return the response.

        The full ``ChatRequest`` is serialized and sent as JSON-RPC params.
        OpenClaw performs the actual LLM call and returns a serialized
        ``ChatResponse``.
        """
        payload = serialize_chat_request(request)

        # Allow per-request timeout override via kwargs
        timeout = kwargs.get("timeout", self._timeout)

        result = await self._rpc.request(
            "openclaw/llm_complete",
            params=payload,
            timeout=timeout,
        )

        return deserialize_chat_response(result)

    def parse_tool_calls(self, response: ChatResponse) -> list[ToolCall]:
        """Extract tool calls from a ChatResponse.

        Tool calls are already normalized by the time they come back from
        OpenClaw, so this is a straightforward extraction with a filter
        for None-argument calls (provider quirk defense).
        """
        if not response.tool_calls:
            return []

        valid = []
        for tc in response.tool_calls:
            if tc.arguments is None:
                logger.debug("Filtering tool call '%s' with None arguments", tc.name)
                continue
            valid.append(tc)
        return valid
