"""Provider routing — match OpenClaw model strings to Amplifier provider modules.

When OpenClaw invokes Amplifier, it passes the current provider/model
(e.g. "anthropic/claude-opus-4-6").  This module resolves which Amplifier
provider module should handle that model, using an ordered routing table
with fnmatch patterns.

Native Amplifier provider modules (provider-anthropic, provider-openai) offer
full feature support (thinking, caching, tool repair, etc.).  The litellm
provider is a universal fallback for any model from any provider.

Routing config example (in amplifier-openclaw settings):

    provider_routing:
      - module: provider-anthropic
        source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
        models:
          - "anthropic/claude-opus-*"
          - "anthropic/claude-sonnet-*"
          - "anthropic/claude-haiku-*"
      - module: provider-openai
        source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
        models:
          - "openai/gpt-4o*"
          - "openai/gpt-4.1*"
          - "openai/o3*"
          - "openai/o4*"
      - module: provider-litellm
        source: git+https://github.com/bkrabach/amplifier-module-provider-litellm@main
        models:
          - "*"
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default routing table — ships with amplifier-app-openclaw
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER_ROUTING: list[dict[str, Any]] = [
    {
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "models": [
            "anthropic/claude-opus-*",
            "anthropic/claude-sonnet-*",
            "anthropic/claude-haiku-*",
        ],
    },
    {
        "module": "provider-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
        "models": [
            "openai/gpt-4o*",
            "openai/gpt-4.1*",
            "openai/o3*",
            "openai/o4*",
        ],
    },
    {
        "module": "provider-litellm",
        "source": "git+https://github.com/bkrabach/amplifier-module-provider-litellm@main",
        "models": [
            "*",
        ],
    },
]


@dataclass
class RoutingEntry:
    """A single entry in the provider routing table.

    Attributes:
        module: Amplifier module ID (e.g. "provider-anthropic").
        source: Module source URI for installation.
        models: List of fnmatch patterns this provider handles.
        config: Optional extra config to pass to the provider module.
    """

    module: str
    source: str
    models: list[str]
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingEntry:
        return cls(
            module=data.get("module", ""),
            source=data.get("source", ""),
            models=data.get("models", []),
            config=data.get("config", {}),
        )


@dataclass
class RoutingResult:
    """Result of provider routing resolution.

    Attributes:
        entry: The matched routing entry.
        model: The original model string that was matched.
        matched_pattern: The specific fnmatch pattern that matched.
    """

    entry: RoutingEntry
    model: str
    matched_pattern: str


def resolve_provider_for_model(
    model: str,
    routing_table: list[RoutingEntry] | None = None,
) -> RoutingResult | None:
    """Resolve which Amplifier provider module should handle a model.

    Walks the routing table top-to-bottom, checks each entry's model
    patterns with fnmatch.  First match wins.

    Args:
        model: Model string from OpenClaw (e.g. "anthropic/claude-opus-4-6").
        routing_table: Ordered list of RoutingEntry.  Falls back to
            DEFAULT_PROVIDER_ROUTING if None.

    Returns:
        RoutingResult with the matched entry, or None if no match
        (shouldn't happen if table has a "*" catch-all).
    """
    if routing_table is None:
        routing_table = load_default_routing_table()

    for entry in routing_table:
        for pattern in entry.models:
            if fnmatch.fnmatch(model, pattern):
                logger.info(
                    "Provider routing: '%s' matched pattern '%s' → %s",
                    model, pattern, entry.module,
                )
                return RoutingResult(
                    entry=entry,
                    model=model,
                    matched_pattern=pattern,
                )

    logger.warning("Provider routing: no match for model '%s'", model)
    return None


def load_routing_table(config: list[dict[str, Any]]) -> list[RoutingEntry]:
    """Load routing table from config dicts.

    Args:
        config: List of routing entry dicts (from settings file).

    Returns:
        List of RoutingEntry objects.
    """
    return [RoutingEntry.from_dict(entry) for entry in config]


def load_default_routing_table() -> list[RoutingEntry]:
    """Load the default routing table, optionally merged with user config.

    Checks for user overrides in:
        ~/.amplifier/openclaw-provider-routing.yaml

    User entries are prepended (higher priority) to the default table.
    The user can override specific providers or add new ones.
    """
    user_entries = _load_user_routing_config()
    default_entries = load_routing_table(DEFAULT_PROVIDER_ROUTING)

    if not user_entries:
        return default_entries

    # User entries go first (higher priority).
    # If a user entry has the same module ID as a default, the default is replaced.
    user_modules = {e.module for e in user_entries}
    filtered_defaults = [e for e in default_entries if e.module not in user_modules]

    merged = user_entries + filtered_defaults
    logger.info(
        "Provider routing: merged %d user entries + %d default entries = %d total",
        len(user_entries), len(filtered_defaults), len(merged),
    )
    return merged


def _load_user_routing_config() -> list[RoutingEntry]:
    """Load user routing config from ~/.amplifier/openclaw-provider-routing.yaml."""
    try:
        import yaml
    except ImportError:
        return []

    config_path = Path.home() / ".amplifier" / "openclaw-provider-routing.yaml"
    if not config_path.exists():
        return []

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        entries = data.get("provider_routing", [])
        if not isinstance(entries, list):
            logger.warning("Invalid provider_routing config: expected list")
            return []

        result = load_routing_table(entries)
        logger.info(
            "Loaded %d user provider routing entries from %s",
            len(result), config_path,
        )
        return result
    except Exception as e:
        logger.warning("Failed to load user routing config: %s", e)
        return []


def build_provider_config_for_model(
    model: str,
    routing_table: list[RoutingEntry] | None = None,
) -> dict[str, Any] | None:
    """Resolve a model and return a provider config dict for mount plan injection.

    This is the main entry point for the runner — given a model string,
    returns a provider config dict ready to inject into a bundle's mount plan.

    Args:
        model: Model string from OpenClaw (e.g. "anthropic/claude-opus-4-6").
        routing_table: Optional custom routing table.

    Returns:
        Provider config dict like:
            {
                "module": "provider-anthropic",
                "source": "git+https://...",
                "config": {"default_model": "claude-opus-4-6", "priority": 0, ...}
            }
        Or None if no match.
    """
    result = resolve_provider_for_model(model, routing_table)
    if result is None:
        return None

    entry = result.entry

    # Build provider config — merge entry's config with model override
    config = dict(entry.config)

    # Normalize model names for the target provider module:
    # 1. Native providers (anthropic, openai) use names WITHOUT prefix
    #    (e.g. "claude-opus-4-6" not "anthropic/claude-opus-4-6")
    # 2. litellm uses its own prefix conventions which may differ from OpenClaw's
    #    (e.g. OpenClaw uses "google/" but litellm uses "gemini/")
    provider_model = _normalize_model_for_provider(model, entry.module)

    config["default_model"] = provider_model
    config["priority"] = 0  # Highest priority — this is the routed provider

    result: dict[str, Any] = {
        "module": entry.module,
        "config": config,
    }

    # Always include source — the module resolver needs it to find or activate
    # the module, even if it's already installed in the venv.
    if entry.source:
        result["source"] = entry.source

    return result


# ---------------------------------------------------------------------------
# Model name normalization
# ---------------------------------------------------------------------------

# OpenClaw and litellm use different provider prefixes for some providers.
# This map translates OpenClaw prefixes → litellm prefixes.
_OPENCLAW_TO_LITELLM_PREFIX: dict[str, str] = {
    "google": "gemini",
    # Add more as needed:
    # "azure": "azure",
}


def _normalize_model_for_provider(model: str, module: str) -> str:
    """Normalize a model name for the target Amplifier provider module.

    - Native providers (anthropic, openai): strip prefix entirely
      ("anthropic/claude-opus-4-6" → "claude-opus-4-6")
    - litellm: translate OpenClaw prefixes to litellm conventions
      ("google/gemini-3-pro-preview" → "gemini/gemini-3-pro-preview")
    - vLLM/OpenAI-compat: keep as-is or strip depending on provider

    Args:
        model: Full model string from OpenClaw (e.g. "google/gemini-3-pro-preview").
        module: Target Amplifier module ID (e.g. "provider-litellm").

    Returns:
        Normalized model name for the target provider.
    """
    if "/" not in model:
        return model

    prefix, model_name = model.split("/", 1)

    if module == "provider-litellm":
        # Translate OpenClaw prefixes to litellm prefixes
        litellm_prefix = _OPENCLAW_TO_LITELLM_PREFIX.get(prefix, prefix)
        return f"{litellm_prefix}/{model_name}"
    else:
        # Native providers: strip prefix
        return model_name
