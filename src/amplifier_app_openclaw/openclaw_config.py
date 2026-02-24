"""Read OpenClaw configuration and build Amplifier provider overlays.

OpenClaw stores provider credentials in auth-profiles.json and model/provider
config in openclaw.json and models.json.  This module reads those files and
produces a Bundle overlay with the appropriate Amplifier provider configuration,
so credentials flow through Amplifier's normal composition → prepare → mount
plan pipeline.

File locations (relative to ~/.openclaw/agents/main/agent/):
    auth-profiles.json  — API keys per provider
    models.json         — provider URLs, API types, model lists
    auth.json           — simpler key-per-provider fallback

File locations (relative to ~/.openclaw/):
    openclaw.json       — global config including default model
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where OpenClaw keeps its agent config
_OPENCLAW_DIR = Path.home() / ".openclaw"
_AGENT_DIR = _OPENCLAW_DIR / "agents" / "main" / "agent"


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning empty dict on any failure."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.debug("Failed to read %s: %s", path, e)
    return {}


def get_openclaw_credentials() -> dict[str, str]:
    """Extract API keys from OpenClaw's auth-profiles.json.

    Returns:
        Dict mapping provider name → API key.
        e.g. {"google": "AIza...", "anthropic": "sk-ant-..."}
    """
    # Try auth-profiles.json first (richer structure)
    profiles_data = _read_json(_AGENT_DIR / "auth-profiles.json")
    profiles = profiles_data.get("profiles", {})

    credentials: dict[str, str] = {}
    for _profile_id, profile in profiles.items():
        if profile.get("type") == "api_key" and profile.get("key"):
            provider = profile.get("provider", "")
            if provider and provider not in credentials:
                credentials[provider] = profile["key"]

    # Fallback to auth.json if auth-profiles.json didn't have what we need
    if not credentials:
        auth_data = _read_json(_AGENT_DIR / "auth.json")
        for provider, entry in auth_data.items():
            if isinstance(entry, dict) and entry.get("type") == "api_key" and entry.get("key"):
                credentials[provider] = entry["key"]

    return credentials


def get_openclaw_default_model() -> str | None:
    """Get the default model from OpenClaw's config.

    Reads openclaw.json → agents.defaults.model.primary

    Returns:
        Model string like "google/gemini-3-pro-preview" or None.
    """
    config = _read_json(_OPENCLAW_DIR / "openclaw.json")
    try:
        return config["agents"]["defaults"]["model"]["primary"]
    except (KeyError, TypeError):
        return None


def get_openclaw_provider_config() -> dict[str, dict[str, Any]]:
    """Get provider configuration (base URLs, API types) from models.json.

    Returns:
        Dict mapping provider name → config dict with keys like
        baseUrl, api, apiKey, models.
    """
    models_data = _read_json(_AGENT_DIR / "models.json")
    return models_data.get("providers", {})


# Map of OpenClaw provider names → env var names that litellm expects.
# Native Amplifier providers (anthropic, openai) accept api_key in config,
# but litellm reads env vars per-provider automatically.
_PROVIDER_TO_ENV_VAR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",  # litellm uses GEMINI_API_KEY for google models
    "xai": "XAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def build_openclaw_provider_overlay(
    model: str | None = None,
) -> dict[str, Any] | None:
    """Build an Amplifier provider config dict from OpenClaw's stored config.

    This is the main entry point.  Given a model string (or the default from
    OpenClaw config), it:
    1. Reads OpenClaw credentials
    2. Routes the model to the appropriate Amplifier provider module
    3. Injects the API key into the provider config (for native providers)
    4. Seeds the env var (for litellm, which reads env vars directly)

    Args:
        model: Model string (e.g. "anthropic/claude-opus-4-6").
               If None, reads the default from OpenClaw config.

    Returns:
        Provider config dict suitable for Bundle(providers=[...]),
        or None if no credentials/config available.
    """
    import os

    from amplifier_app_openclaw.provider_routing import build_provider_config_for_model

    # Determine which model to configure for
    if not model:
        model = get_openclaw_default_model()
    if not model:
        logger.info("No model specified and no OpenClaw default model found")
        return None

    # Get the routed provider config
    routed = build_provider_config_for_model(model)
    if not routed:
        logger.warning("No provider routing match for model '%s'", model)
        return None

    # Get credentials from OpenClaw
    credentials = get_openclaw_credentials()
    if not credentials:
        logger.info("No OpenClaw credentials found in auth-profiles.json")
        return routed  # Return routing without credentials — might work via env vars

    # Determine which credential to inject based on the model prefix
    provider_prefix = model.split("/")[0] if "/" in model else model
    api_key = credentials.get(provider_prefix)

    if api_key:
        module = routed.get("module", "")

        if module == "provider-litellm":
            # litellm reads API keys from env vars, not from config.
            # Seed the appropriate env var if not already set.
            env_var = _PROVIDER_TO_ENV_VAR.get(provider_prefix)
            if env_var and not os.environ.get(env_var):
                os.environ[env_var] = api_key
                logger.info(
                    "Seeded %s from OpenClaw credentials for litellm provider",
                    env_var,
                )
        else:
            # Native providers (anthropic, openai) accept api_key in config
            routed.setdefault("config", {})["api_key"] = api_key
            logger.info(
                "Injected OpenClaw credential for provider '%s' into %s config",
                provider_prefix, module,
            )
    else:
        logger.debug(
            "No OpenClaw credential found for provider prefix '%s' (have: %s)",
            provider_prefix, list(credentials.keys()),
        )

    return routed
