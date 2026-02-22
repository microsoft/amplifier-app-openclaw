# amplifier-app-openclaw

**OpenClaw is where your AI lives. Amplifier is how your AI thinks.**

`amplifier-app-openclaw` is the integration layer that connects [OpenClaw](https://github.com/openclaw) — a personal AI agent runtime — with [Microsoft Amplifier](https://github.com/microsoft/amplifier-core), a framework for composable AI agent behaviors called "bundles." It provides a CLI (and eventually a JSON-RPC sidecar) that lets OpenClaw delegate complex, long-running tasks to specialized Amplifier agents — things like deep research, multi-step coding, data analysis, and structured problem-solving — while keeping the lightweight conversational agent fast and responsive.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repository
git clone https://github.com/bkrabach/amplifier-app-openclaw.git
cd amplifier-app-openclaw

# Install with uv (handles all dependencies including git sources)
uv sync

# Verify installation
uv run amplifier-openclaw --version
```

## Quick Start

Set your API key and run a task:

```bash
export OPENAI_API_KEY="sk-..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."

# Run a simple prompt
uv run amplifier-openclaw run "What are the three laws of thermodynamics?"
```

Output is structured JSON on stdout (status messages go to stderr):

```json
{
  "response": "The three laws of thermodynamics are...",
  "usage": {
    "input_tokens": 42,
    "output_tokens": 256,
    "estimated_cost": 0.003,
    "tool_invocations": 0
  },
  "status": "completed"
}
```

## CLI Reference

### `amplifier-openclaw`

Top-level command group. Shows help when called without a subcommand.

```bash
amplifier-openclaw --help
amplifier-openclaw --version
```

### `amplifier-openclaw run`

Run a single prompt through an Amplifier session. Outputs JSON to stdout.

```bash
# Basic usage
amplifier-openclaw run "Summarize the key ideas in this codebase"

# Specify a bundle
amplifier-openclaw run --bundle foundation "List 5 creative project ideas"

# Custom working directory and timeout
amplifier-openclaw run --cwd /path/to/project --timeout 600 "Refactor the auth module"
```

| Option | Default | Description |
|---|---|---|
| `--bundle` | `foundation` | Bundle name to load |
| `--cwd` | `.` | Working directory for the session |
| `--timeout` | `300` | Timeout in seconds |

### `amplifier-openclaw bundles list`

List all registered bundles as JSON.

```bash
# List all bundles
amplifier-openclaw bundles list

# Root bundles only (no sub-behaviors)
amplifier-openclaw bundles list --root-only
```

### `amplifier-openclaw bundles add`

Add a bundle from a git URI or local path.

```bash
# From a git repository
amplifier-openclaw bundles add git+https://github.com/org/my-bundle@main

# From a local directory
amplifier-openclaw bundles add ./my-local-bundle
```

## Available Bundles

Bundles are Amplifier's unit of composable agent behavior. Key bundles available through `amplifier-foundation`:

| Bundle | Description |
|---|---|
| `foundation` | General-purpose reasoning agent (default) |
| `coder` | Code generation, refactoring, and debugging |
| `researcher` | Deep research with web search and synthesis |
| `writer` | Long-form content creation and editing |

Use `amplifier-openclaw bundles list` to see all registered bundles in your environment.

## Configuration

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | One of these | OpenAI API key |
| `ANTHROPIC_API_KEY` | required | Anthropic API key |
| `AMPLIFIER_MODEL` | No | Override the default model |
| `NO_COLOR` | No | Set automatically in CLI mode to disable color output |

### API Keys

At least one LLM API key must be set. The bundle determines which provider is used — `foundation` typically uses whichever key is available.

## Phase Roadmap

| Phase | Focus | Status |
|---|---|---|
| **Phase 0** | CLI (`amplifier-openclaw run`) — single-shot task execution, JSON output, cost tracking | ✅ Current |
| **Phase 1** | JSON-RPC sidecar (`amplifier-openclaw serve`) — persistent process, session management, OpenClaw tool integration | 🔜 Next |
| **Phase 1.5** | Advanced features — streaming, multi-agent delegation, approval flows | 📋 Planned |
| **Phase 2** | Native integration — embedded Python runtime, zero-overhead calls | 💭 Future |

## Development Setup

```bash
# Clone and install
git clone https://github.com/bkrabach/amplifier-app-openclaw.git
cd amplifier-app-openclaw
uv sync

# Run tests
uv run pytest tests/

# Run a specific test file
uv run pytest tests/test_cli.py -v

# Run with integration tests (requires API keys)
uv run pytest tests/ -m integration
```

### Project Structure

```
src/amplifier_app_openclaw/
├── __init__.py          # Package version
├── cli.py               # Click CLI entry point
├── runner.py            # Session lifecycle (load → execute → cleanup)
├── spawn.py             # Agent delegation stub (Phase 0)
├── adapters/            # OpenClaw-specific adapters
├── automation/          # Automation utilities
└── tools/               # Tool integrations
```

## License

TBD
