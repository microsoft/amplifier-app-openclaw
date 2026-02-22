---
name: amplifier-openclaw
description: "Delegate complex tasks to Amplifier's multi-agent framework. Use when: (1) research/comparison needing multiple perspectives, (2) multi-file code projects, (3) architecture/design reviews, (4) user asks for deep/thorough work. NOT for: simple Q&A, quick edits, casual chat, anything needing <5s response. CLI: amplifier-openclaw."
metadata:
  {
    "openclaw": { "emoji": "⚡", "requires": { "anyBins": ["amplifier-openclaw"] } },
  }
---

# Amplifier — Multi-Agent Delegation

Amplifier is a multi-agent AI framework. It handles tasks that benefit from specialist agents, structured workflows, or parallel investigation.

Two modes are available:
- **Phase 0 (CLI):** Direct `amplifier-openclaw run` — simple, stateless, one task at a time
- **Phase 1 (Sidecar):** Persistent JSON-RPC sidecar via helper scripts — streaming, governance, recipes

## When to Delegate

**High confidence → delegate immediately:**
- "Research X and compare approaches" → `--bundle foundation`
- "Build a Python tool that does X" → `--bundle python-dev`
- "Review this code for security and design" → `--bundle design-intelligence`
- User says "amplifier", "deep dive", "thorough", "comprehensive"
- Task has clear subtasks that benefit from parallel agents

**Medium confidence → offer the choice:**
- "Analyze this codebase" → "I can do a quick analysis, or delegate to Amplifier for a thorough multi-agent review."

**Low confidence → handle yourself:**
- Simple Q&A, quick code edits, casual conversation, anything needing immediate response

## Phase 0 — Direct CLI (Default)

### Quick Start

```bash
# Delegate a task
exec command:"amplifier-openclaw run 'Research the top 3 Python web frameworks' --bundle foundation" background:true timeout:600
```

### Or use the wrapper script:

```bash
exec command:"amplifier-tool.sh delegate --bundle foundation 'Research local-first AI approaches'" background:true timeout:600
```

### CLI Reference

```bash
amplifier-openclaw run "prompt" --bundle NAME --cwd PATH --timeout SECS
amplifier-openclaw bundles list [--root-only]
amplifier-openclaw bundles add <source>
amplifier-openclaw cost [--session ID] [--period today|week|month]
```

### JSON Output

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

## Phase 1 — Sidecar Mode (Advanced)

For streaming progress, governance evaluation, and automation recipes, use the sidecar scripts.

### Sidecar Lifecycle

```bash
# Start the sidecar (persists across requests)
exec command:"sidecar-manager.sh start"

# Check status
exec command:"sidecar-manager.sh status"

# Stop when done
exec command:"sidecar-manager.sh stop"
```

The sidecar auto-starts if needed when using `amplifier-tool.sh` with `AMPLIFIER_USE_SIDECAR=1`.

### Delegating via Sidecar

```bash
exec command:"AMPLIFIER_USE_SIDECAR=1 amplifier-tool.sh delegate --bundle foundation 'Research X'" background:true timeout:600
```

### Governance Evaluation (Phase 1)

Before running a potentially risky tool call, evaluate it:

```bash
exec command:'AMPLIFIER_USE_SIDECAR=1 amplifier-tool.sh evaluate "{\"tool\":\"exec\",\"args\":{\"command\":\"rm -rf /tmp/data\"}}"'
```

Returns `{"action":"deny","reason":"..."}`, `{"action":"ask_user","reason":"..."}`, or `{"action":"continue"}`.

### Automation Recipes (Phase 1)

Run multi-step workflows:

```bash
exec command:'AMPLIFIER_USE_SIDECAR=1 amplifier-tool.sh recipe morning-briefing "{\"channel\":\"whatsapp\"}"'
```

## Available Bundles

| Bundle | Best For |
|--------|----------|
| `foundation` | General: research, analysis, planning, comparison |
| `python-dev` | Python projects: build, debug, test, review |
| `design-intelligence` | Architecture & design review, code quality |
| `recipes` | Multi-step declarative workflows |

**Default to `foundation`** unless the task clearly fits another bundle.

## Interpreting Results

- **`response`**: The main result — present this to the user
- **`usage.estimated_cost`**: May be `0.0`; don't alarm about zero-cost results
- **`error`**: If present, report in plain language, don't dump raw JSON
- **`session_id`**: Save for potential follow-up work

## Running in Background

For tasks >30 seconds:

```bash
exec command:"amplifier-tool.sh delegate 'Build a REST API for todo items' --bundle python-dev --timeout 600" background:true
```

Monitor with `process action:log sessionId:XXX` and report results when complete.

## During Active Delegation

- **"stop"/"cancel"** → kill the background process
- **Unrelated questions** → answer yourself, don't interrupt Amplifier
- **Follow-up** → tell user you'll pass it along when current task finishes

## Cost Reporting

```bash
exec command:"amplifier-tool.sh cost --period week"
```

Report costs only when asked or when notable (>$1).

## Duration Awareness

Typical delegation times:
- Simple research: 30–60 seconds
- Code project: 2–5 minutes
- Deep analysis: 1–3 minutes

On channels without streaming (WhatsApp), delegate only if the task genuinely needs multi-agent power.
