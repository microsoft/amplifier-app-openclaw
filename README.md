# amplifier-app-openclaw

**OpenClaw is where your AI lives. Amplifier is how your AI thinks.**

`amplifier-app-openclaw` integrates [OpenClaw](https://github.com/openclaw/openclaw) — a personal AI agent runtime — with [Microsoft Amplifier](https://github.com/microsoft/amplifier-core), a framework for composable AI agent behaviors. It lets OpenClaw delegate complex tasks to Amplifier agents while automatically routing to the best LLM provider for each model.

## Key Features

- **Provider Routing** — Automatically matches any LLM model to the best Amplifier provider module. Native providers (Anthropic, OpenAI) get full features (thinking, caching, tool repair); everything else falls through to litellm.
- **100+ LLM Providers** — Any model OpenClaw has configured works automatically via [provider-litellm](https://github.com/bkrabach/amplifier-module-provider-litellm). No Amplifier-specific API keys needed.
- **Composable Bundles** — Amplifier's modular bundles (foundation, superpowers, coder, etc.) work out of the box.
- **Session Persistence** — Resume named sessions across invocations.
- **JSON-RPC Sidecar** — Persistent bridge for bidirectional OpenClaw ↔ Amplifier communication.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/bkrabach/amplifier-app-openclaw.git
cd amplifier-app-openclaw
uv sync
uv run amplifier-openclaw --version
```

## Quick Start

```bash
# Set at least one API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Run with default provider (auto-detected from settings)
uv run amplifier-openclaw run "Explain the three laws of thermodynamics"

# Run with a specific model — routing picks the best provider automatically
uv run amplifier-openclaw run --model anthropic/claude-opus-4-6 "Deep analysis of this codebase"
uv run amplifier-openclaw run --model gemini/gemini-2.5-flash "Quick summary of recent changes"
uv run amplifier-openclaw run --model xai/grok-3 "What's happening in AI today?"

# Use a specific bundle
uv run amplifier-openclaw run --bundle superpowers "Research quantum computing advances"
```

Output is structured JSON on stdout (status on stderr):

```json
{
  "response": "The three laws of thermodynamics are...",
  "usage": {
    "input_tokens": 28566,
    "output_tokens": 129,
    "estimated_cost": 0.003,
    "tool_invocations": 0
  },
  "status": "completed"
}
```

## Provider Routing

When you specify `--model`, the routing table matches it to the best Amplifier provider module:

| Model Pattern | Provider Module | Features |
|---|---|---|
| `anthropic/claude-opus-*` | provider-anthropic | Extended thinking, prompt caching, 1M context, tool repair |
| `anthropic/claude-sonnet-*` | provider-anthropic | Extended thinking, prompt caching, 1M context |
| `anthropic/claude-haiku-*` | provider-anthropic | Fast inference, prompt caching |
| `openai/gpt-4o*` | provider-openai | Responses API, reasoning |
| `openai/o3*`, `openai/o4*` | provider-openai | Responses API, reasoning |
| `gemini/*` | provider-litellm | Via litellm (env vars) |
| `ollama/*` | provider-litellm | Local models, no API key needed |
| `groq/*`, `xai/*`, `openrouter/*` | provider-litellm | Via litellm (env vars) |
| `*` (anything else) | provider-litellm | Universal fallback |

First match wins. Native providers get full provider-specific features. litellm covers the long tail (100+ providers via standard environment variables).

### Extending the Routing Table

Add custom provider modules via `~/.amplifier/openclaw-provider-routing.yaml`:

```yaml
provider_routing:
  # Community Mistral provider with native function calling
  - module: provider-mistral
    source: git+https://github.com/someone/amplifier-module-provider-mistral
    models:
      - "mistral/*"
```

User entries are prepended to the default table (higher priority). Same module IDs replace defaults.

## CLI Reference

### `amplifier-openclaw run`

```bash
amplifier-openclaw run [OPTIONS] PROMPT
```

| Option | Default | Description |
|---|---|---|
| `--model` | (from settings) | Model to use (e.g. `anthropic/claude-opus-4-6`). Auto-routes to best provider. |
| `--bundle` | `foundation` | Amplifier bundle to load |
| `--cwd` | `.` | Working directory for the session |
| `--timeout` | `300` | Timeout in seconds |
| `--persistent` | off | Enable session persistence |
| `--session-name` | (none) | Named session (implies `--persistent`) |
| `--resume` | off | Resume a named session |

### `amplifier-openclaw serve`

Start the JSON-RPC sidecar for persistent OpenClaw integration.

```bash
# Unix socket mode (for sidecar)
amplifier-openclaw serve --socket /tmp/amplifier.sock

# Stdin/stdout mode (for subprocess invocation)
amplifier-openclaw serve
```

### `amplifier-openclaw bundles`

```bash
amplifier-openclaw bundles list [--root-only]
amplifier-openclaw bundles add SOURCE
```

### `amplifier-openclaw cost`

```bash
amplifier-openclaw cost [--period day|week|month|all] [--session ID]
```

## Architecture

```
OpenClaw agent
  │
  ├─ amplifier-openclaw run --model gemini/gemini-2.5-flash "task"
  │     │
  │     ├─ provider_routing.py    → fnmatch model → provider module
  │     ├─ runner.py              → load bundle, create session, execute
  │     └─ provider-litellm      → litellm.acompletion() → Gemini API
  │
  ├─ amplifier-openclaw serve --socket /tmp/amp.sock
  │     │
  │     ├─ session_manager.py     → JSON-RPC session lifecycle
  │     ├─ tools/                 → OpenClaw tools (browser, message, memory, devices, cron)
  │     └─ automation/            → Recipe execution
  │
  └─ JSON output → OpenClaw parses response + usage
```

### Provider Routing Flow

```
"anthropic/claude-opus-4-6"
  → routing table: anthropic/claude-opus-* matches
    → provider-anthropic (full thinking, caching, 1M context)
      → Anthropic API (using ANTHROPIC_API_KEY from env)

"gemini/gemini-2.5-flash"
  → routing table: no specific match
    → * matches → provider-litellm
      → litellm.acompletion() (using GEMINI_API_KEY from env)

"ollama/llama3.2"
  → routing table: no specific match
    → * matches → provider-litellm
      → litellm.acompletion() (using OLLAMA_API_BASE from env)
      → No API key needed, no cost
```

## Development

```bash
git clone https://github.com/bkrabach/amplifier-app-openclaw.git
cd amplifier-app-openclaw
uv sync

# Run tests (232 passing)
uv run python -m pytest tests/ -v

# Run specific test suite
uv run python -m pytest tests/test_provider_routing.py -v
```

### Project Structure

```
src/amplifier_app_openclaw/
├── cli.py                  # Click CLI entry point
├── runner.py               # Session lifecycle (load → execute → cleanup)
├── provider_routing.py     # Model → provider module resolution
├── serve.py                # JSON-RPC sidecar
├── session_manager.py      # Session lifecycle over RPC
├── spawn.py                # Agent delegation
├── cost.py                 # Usage/cost tracking
├── adapters/               # Protocol adapters (display, approval, streaming, spawn)
├── automation/             # Recipe execution
├── modules/                # Amplifier modules (tool-openclaw)
│   └── tool_openclaw.py    # OpenClaw tools as Amplifier tools
└── tools/                  # Tool bridge implementations
    ├── browser.py
    ├── cron.py
    ├── devices.py
    ├── memory.py
    └── message.py
```

## Related Projects

- **[amplifier-core](https://github.com/microsoft/amplifier-core)** — Amplifier's kernel: session lifecycle, module loading, coordinator
- **[amplifier-foundation](https://github.com/microsoft/amplifier-foundation)** — Bundle system, spawn utilities, module resolution
- **[amplifier-module-provider-litellm](https://github.com/bkrabach/amplifier-module-provider-litellm)** — Universal LLM provider via litellm (100+ providers)
- **[OpenClaw](https://github.com/openclaw/openclaw)** — Personal AI agent runtime

## License

MIT
