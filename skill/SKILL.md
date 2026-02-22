---
name: amplifier-openclaw
description: "Delegate complex tasks to Amplifier's multi-agent framework. Use when: (1) research/comparison needing multiple perspectives, (2) multi-file code projects, (3) architecture/design reviews, (4) user asks for deep/thorough work. NOT for: simple Q&A, quick edits, casual chat, anything needing <5s response. CLI: amplifier-openclaw."
metadata:
  {
    "openclaw": { "emoji": "⚡", "requires": { "anyBins": ["amplifier-openclaw"] } },
  }
---

# Amplifier — Multi-Agent Delegation

Amplifier is a multi-agent AI framework. It handles tasks that benefit from specialist agents, structured workflows, or parallel investigation. You invoke it via the `amplifier-openclaw` CLI.

## Quick Start

```bash
# Delegate a task (runs in background, outputs JSON to stdout)
exec command:"amplifier-openclaw run 'Research the top 3 Python web frameworks and compare them'" background:true timeout:600
```

## When to Delegate

**High confidence → delegate immediately:**

- "Research X and compare approaches" → `--bundle foundation`
- "Build a Python tool that does X" → `--bundle python-dev`
- "Review this code for security and design" → `--bundle design-intelligence`
- User says "amplifier", "deep dive", "thorough", "comprehensive"
- Task has clear subtasks that benefit from parallel agents

**Medium confidence → offer the choice:**

- "Analyze this codebase" → "I can do a quick analysis, or delegate to Amplifier for a thorough multi-agent review. Which do you prefer?"
- Complex project planning → try yourself first; if it's big, suggest delegation

**Low confidence → handle yourself:**

- Simple Q&A, writing, brainstorming, summarization
- Quick code edits or explanations
- Casual conversation
- Anything needing immediate response (<5 seconds)
- The user is already getting good results from you directly

## Duration Awareness

On channels without streaming (WhatsApp), delegate only if the task genuinely needs multi-agent power. A 3-second Amplifier lookup is worse UX than handling it yourself in 1 second. When in doubt, handle it yourself.

Typical delegation times:
- Simple research: 30–60 seconds
- Code project: 2–5 minutes
- Deep analysis: 1–3 minutes

## CLI Reference

### `amplifier-openclaw run <prompt>`

Run a single prompt through an Amplifier session. Outputs JSON to stdout, logs to stderr.

```bash
amplifier-openclaw run "Your task description" \
  --bundle foundation \
  --cwd /path/to/project \
  --timeout 300
```

| Option | Default | Description |
|--------|---------|-------------|
| `--bundle` | `foundation` | Which capability bundle to use |
| `--cwd` | `.` | Working directory for the session |
| `--timeout` | `300` | Max seconds before timeout |

**JSON output:**

```json
{
  "response": "The analysis found...",
  "session_id": "abc-123",
  "usage": {
    "input_tokens": 4200,
    "output_tokens": 1800,
    "estimated_cost": 0.12,
    "tool_invocations": 3
  },
  "duration_seconds": 45.2,
  "bundle": "foundation"
}
```

On error:

```json
{
  "error": "Bundle 'foo' not found",
  "error_type": "BundleNotFoundError"
}
```

### `amplifier-openclaw bundles list`

List available bundles as JSON.

```bash
amplifier-openclaw bundles list
amplifier-openclaw bundles list --root-only
```

Output is a JSON array:

```json
[
  { "name": "foundation", "source": "", "version": "2.0.0", "status": "cached", "is_root": true },
  { "name": "python-dev", "source": "git+https://...", "version": "1.0.0", "status": "registered", "is_root": true }
]
```

### `amplifier-openclaw bundles add <source>`

Add a bundle from a git URI or local path.

```bash
amplifier-openclaw bundles add git+https://github.com/org/bundle@main
amplifier-openclaw bundles add ./my-local-bundle
```

### `amplifier-openclaw cost`

Show cost report as JSON. *(Available once cost tracking is enabled.)*

```bash
amplifier-openclaw cost
amplifier-openclaw cost --period week
amplifier-openclaw cost --session abc-123
```

## Available Bundles

| Bundle | Best For |
|--------|----------|
| `foundation` | General multi-agent: research, analysis, planning, comparison |
| `python-dev` | Python projects: build, debug, test, review |
| `design-intelligence` | Architecture & design review, code quality analysis |
| `recipes` | Multi-step declarative workflows (morning briefing, etc.) |

**Default to `foundation`** unless the task clearly fits another bundle. When unsure, `foundation` handles most things well.

## Interpreting Results

- **`response`**: The main result text — present this to the user
- **`usage.estimated_cost`**: May be `0.0` if the provider doesn't report costs; don't alarm the user about zero-cost results
- **`error`**: If present, the task failed — report `error` in plain language, don't dump raw JSON
- **`session_id`**: Save this if the user might want follow-up work on the same context

## Running in Background

For tasks that take >30 seconds, run in background mode:

```bash
exec command:"amplifier-openclaw run 'Build a REST API for todo items' --bundle python-dev --timeout 600" background:true
```

Then monitor with `process action:log sessionId:XXX` and report results when complete.

## During Active Delegation

- **"stop" / "cancel" / "nevermind"** → kill the background process
- **Unrelated questions** → answer yourself, don't interrupt Amplifier
- **Follow-up to the running task** → tell the user you'll pass it along when the current task finishes

## Cost Reporting

After delegated tasks, you can proactively mention costs:

> "That research task used ~4,200 tokens and cost about $0.12."

Don't report costs unless the user asks or the cost seems notable (>$1).

## Progressive Disclosure

Start simple. Expand as you learn what works:

1. **Week 1**: Delegate obvious complex tasks (research, multi-file code). Use `foundation` bundle for everything.
2. **Week 2**: Start matching bundles to tasks (`python-dev` for Python work). Report costs when asked.
3. **Month 1**: Suggest specific bundles proactively. Use `bundles list` to show what's available.
4. **Month 2**: Recommend `bundles add` for custom bundles based on the user's recurring patterns.
