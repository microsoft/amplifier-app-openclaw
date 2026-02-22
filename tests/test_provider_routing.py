"""Tests for provider routing — model → Amplifier provider module resolution."""

from __future__ import annotations

import pytest
from amplifier_app_openclaw.provider_routing import (
    DEFAULT_PROVIDER_ROUTING,
    RoutingEntry,
    RoutingResult,
    build_provider_config_for_model,
    load_routing_table,
    resolve_provider_for_model,
)


@pytest.fixture
def routing_table() -> list[RoutingEntry]:
    return load_routing_table(DEFAULT_PROVIDER_ROUTING)


class TestResolveProviderForModel:
    """Test model → provider routing resolution."""

    def test_anthropic_opus(self, routing_table):
        result = resolve_provider_for_model("anthropic/claude-opus-4-6", routing_table)
        assert result is not None
        assert result.entry.module == "provider-anthropic"
        assert result.model == "anthropic/claude-opus-4-6"

    def test_anthropic_sonnet(self, routing_table):
        result = resolve_provider_for_model("anthropic/claude-sonnet-4-20250514", routing_table)
        assert result is not None
        assert result.entry.module == "provider-anthropic"

    def test_anthropic_haiku(self, routing_table):
        result = resolve_provider_for_model("anthropic/claude-haiku-3-5-20240307", routing_table)
        assert result is not None
        assert result.entry.module == "provider-anthropic"

    def test_openai_gpt4o(self, routing_table):
        result = resolve_provider_for_model("openai/gpt-4o", routing_table)
        assert result is not None
        assert result.entry.module == "provider-openai"

    def test_openai_gpt4o_mini(self, routing_table):
        result = resolve_provider_for_model("openai/gpt-4o-mini", routing_table)
        assert result is not None
        assert result.entry.module == "provider-openai"

    def test_openai_o3(self, routing_table):
        result = resolve_provider_for_model("openai/o3", routing_table)
        assert result is not None
        assert result.entry.module == "provider-openai"

    def test_openai_o4_mini(self, routing_table):
        result = resolve_provider_for_model("openai/o4-mini", routing_table)
        assert result is not None
        assert result.entry.module == "provider-openai"

    def test_openai_gpt41(self, routing_table):
        result = resolve_provider_for_model("openai/gpt-4.1", routing_table)
        assert result is not None
        assert result.entry.module == "provider-openai"

    def test_openai_gpt35_falls_through_to_litellm(self, routing_table):
        """gpt-3.5-turbo doesn't match openai patterns → falls to litellm."""
        result = resolve_provider_for_model("openai/gpt-3.5-turbo", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_gemini_falls_to_litellm(self, routing_table):
        """No native Gemini provider → litellm handles it."""
        result = resolve_provider_for_model("gemini/gemini-2.5-pro", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_ollama_falls_to_litellm(self, routing_table):
        result = resolve_provider_for_model("ollama/llama3.2", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_groq_falls_to_litellm(self, routing_table):
        result = resolve_provider_for_model("groq/llama-3.3-70b-versatile", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_xai_falls_to_litellm(self, routing_table):
        result = resolve_provider_for_model("xai/grok-3", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_openrouter_falls_to_litellm(self, routing_table):
        result = resolve_provider_for_model("openrouter/meta-llama/llama-3-70b", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"

    def test_unknown_falls_to_litellm(self, routing_table):
        result = resolve_provider_for_model("some-random-provider/some-model", routing_table)
        assert result is not None
        assert result.entry.module == "provider-litellm"


class TestFirstMatchWins:
    """Verify ordering — first match wins, not best match."""

    def test_user_entry_overrides_default(self):
        """User entry for anthropic/* should win over default."""
        custom_table = load_routing_table([
            {
                "module": "provider-custom-anthropic",
                "source": "local",
                "models": ["anthropic/*"],
            },
            {
                "module": "provider-anthropic",
                "source": "git+...",
                "models": ["anthropic/claude-opus-*"],
            },
            {
                "module": "provider-litellm",
                "source": "git+...",
                "models": ["*"],
            },
        ])
        result = resolve_provider_for_model("anthropic/claude-opus-4-6", custom_table)
        assert result is not None
        assert result.entry.module == "provider-custom-anthropic"

    def test_specific_before_wildcard(self):
        """More specific patterns should be listed before wildcards."""
        table = load_routing_table([
            {
                "module": "provider-anthropic",
                "source": "git+...",
                "models": ["anthropic/claude-sonnet-*"],
            },
            {
                "module": "provider-litellm",
                "source": "git+...",
                "models": ["anthropic/*", "*"],
            },
        ])
        # Sonnet matches first entry
        result = resolve_provider_for_model("anthropic/claude-sonnet-4-5", table)
        assert result.entry.module == "provider-anthropic"

        # Opus doesn't match first entry, falls to litellm
        result = resolve_provider_for_model("anthropic/claude-opus-4-6", table)
        assert result.entry.module == "provider-litellm"


class TestBuildProviderConfig:
    """Test the mount-plan-ready config builder."""

    def test_returns_config_for_known_model(self):
        table = load_routing_table(DEFAULT_PROVIDER_ROUTING)
        config = build_provider_config_for_model("anthropic/claude-opus-4-6", table)
        assert config is not None
        assert config["module"] == "provider-anthropic"
        assert config["config"]["default_model"] == "anthropic/claude-opus-4-6"
        assert config["config"]["priority"] == 0

    def test_returns_litellm_for_unknown(self):
        table = load_routing_table(DEFAULT_PROVIDER_ROUTING)
        config = build_provider_config_for_model("ollama/llama3.2", table)
        assert config is not None
        assert config["module"] == "provider-litellm"
        assert config["config"]["default_model"] == "ollama/llama3.2"

    def test_merges_entry_config(self):
        """Extra config from routing entry should be merged."""
        table = load_routing_table([
            {
                "module": "provider-anthropic",
                "source": "git+...",
                "models": ["anthropic/*"],
                "config": {"enable_prompt_caching": True, "enable_1m_context": True},
            },
        ])
        config = build_provider_config_for_model("anthropic/claude-opus-4-6", table)
        assert config["config"]["enable_prompt_caching"] is True
        assert config["config"]["enable_1m_context"] is True
        assert config["config"]["default_model"] == "anthropic/claude-opus-4-6"

    def test_returns_none_with_empty_table(self):
        config = build_provider_config_for_model("anything", [])
        assert config is None


class TestRoutingEntry:
    def test_from_dict(self):
        entry = RoutingEntry.from_dict({
            "module": "provider-test",
            "source": "local",
            "models": ["test/*"],
            "config": {"key": "val"},
        })
        assert entry.module == "provider-test"
        assert entry.models == ["test/*"]
        assert entry.config == {"key": "val"}

    def test_from_dict_defaults(self):
        entry = RoutingEntry.from_dict({"module": "p", "source": "s", "models": ["*"]})
        assert entry.config == {}
