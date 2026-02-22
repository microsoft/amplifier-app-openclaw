# Phase 2 Implementation Plan — Bidirectional Integration

## Overview

Phase 2 makes the integration truly bidirectional:
- **Task 2.1**: `provider-openclaw` — Amplifier routes LLM calls through OpenClaw's provider layer
- **Task 2.2**: OpenClaw tools as proper Amplifier modules (not just sidecar bridges)
- **Task 2.3**: Dynamic capability discovery at runtime

## Implementation Order

**Task 2.2 first** (tools as modules) → **Task 2.3** (discovery) → **Task 2.1** (provider) 

Rationale: 2.2 establishes the module packaging pattern. 2.3 builds on it for dynamic registration. 2.1 is the most complex and benefits from both foundations.

---

## Task 2.2 — OpenClaw Tools as Amplifier Modules

### What exists today
- 5 tool bridges in `src/amplifier_app_openclaw/tools/`: message, browser, memory, devices, cron
- Each extends `OpenClawToolBase` which conforms to Amplifier's `Tool` Protocol (name, description, input_schema, execute)
- Tools are manually instantiated in `session_manager.py` `_register_openclaw_tools()` and mounted via `coordinator.mount("tools", ...)`
- Tools need a `JsonRpcResponseReader` to communicate with OpenClaw

### What needs to change
Package each tool bridge as a mountable Amplifier module with `__amplifier_module_type__ = "tool"` and `async mount(coordinator, config)`.

### Files

**New: `src/amplifier_app_openclaw/modules/__init__.py`**
- Package marker

**New: `src/amplifier_app_openclaw/modules/tool_openclaw.py`**
- Single Amplifier module that mounts ALL 5 OpenClaw tools
- `__amplifier_module_type__ = "tool"`
- `async mount(coordinator, config)` — creates tool instances using RPC reader from coordinator capability
- Config: `{"tools": ["message", "browser", "memory", "devices", "cron"]}` (whitelist)
- Reads `JsonRpcResponseReader` from coordinator capability `"openclaw.rpc_reader"`

**Modified: `src/amplifier_app_openclaw/session_manager.py`**
- In `handle_create()`, register `"openclaw.rpc_reader"` as coordinator capability before session init
- Remove manual `_register_openclaw_tools()` call — let the module system handle it
- Add `tool-openclaw` to the mount_plan's tools list

**New: `tests/test_module_tool_openclaw.py`**
- Test mount() creates correct tools based on config whitelist
- Test mount() reads rpc_reader from coordinator capability
- Test mount() fails gracefully if rpc_reader not available
- Test default config mounts all 5 tools

**Modified: `pyproject.toml`**
- Add entry point: `tool-openclaw = amplifier_app_openclaw.modules.tool_openclaw:mount`

### Acceptance Criteria
- [ ] `tool-openclaw` module loadable via Amplifier's module system
- [ ] Tools work identically to current manual mounting
- [ ] Config-driven tool whitelist
- [ ] Existing tests still pass

---

## Task 2.3 — Bidirectional Capability Discovery

### What needs to happen
1. Amplifier queries OpenClaw for available tools/capabilities at session create
2. OpenClaw queries Amplifier for available bundles/tools at sidecar connect

### Files

**New: `src/amplifier_app_openclaw/discovery.py`**
- `async discover_openclaw_tools(rpc_reader) -> list[ToolSpec]` — calls `openclaw/tools_list` JSON-RPC
- `async register_amplifier_tools(writer, tools) -> None` — calls `openclaw/tools_register` to expose Amplifier tools to OpenClaw
- `ToolSpec` dataclass: name, description, input_schema

**Modified: `src/amplifier_app_openclaw/session_manager.py`**
- After session create, call `register_amplifier_tools()` to expose the session's tools back to OpenClaw
- New handler: `augment/list_tools` — returns all tools available in a session

**New: `src/amplifier_app_openclaw/modules/tool_openclaw_dynamic.py`**
- Dynamic variant of tool-openclaw that discovers tools at mount time via RPC
- Creates `OpenClawToolBase` instances dynamically from discovered specs

**New: `tests/test_discovery.py`**
- Test discover_openclaw_tools with mock RPC
- Test register_amplifier_tools sends correct RPC calls
- Test dynamic tool creation from specs

### Acceptance Criteria
- [ ] OpenClaw tools discovered dynamically (not hardcoded)
- [ ] Amplifier tools registered back to OpenClaw
- [ ] Tool list queryable via `augment/list_tools`

---

## Task 2.1 — provider-openclaw Module

### What it does
Routes Amplifier's LLM calls through OpenClaw's gateway, letting Amplifier use whatever provider/model OpenClaw has configured.

### Key Technical Challenges
1. **Serialization**: `ChatRequest` (Pydantic) → JSON-RPC → OpenClaw → provider → response → `ChatResponse` (Pydantic)
2. **Streaming**: OpenClaw may stream responses; need to buffer or relay SSE chunks
3. **Tool call parsing**: Different providers format tool calls differently; the provider module must normalize
4. **Model listing**: Query OpenClaw for available models

### Files

**New: `src/amplifier_app_openclaw/modules/provider_openclaw.py`**
- `__amplifier_module_type__ = "provider"`
- `async mount(coordinator, config)` — creates and registers the provider
- `OpenClawProvider` class implementing the `Provider` protocol:
  - `name` → `"openclaw"`
  - `async get_info()` → `ProviderInfo` with model list from OpenClaw
  - `async list_models()` → query OpenClaw for available models
  - `async complete(request: ChatRequest) -> ChatResponse` — serialize request, send via RPC `openclaw/llm_complete`, deserialize response
  - `parse_tool_calls(response) -> list[ToolCall]` — extract tool calls from response content
- Config: `{"model": "default", "timeout": 120}`

**New: `src/amplifier_app_openclaw/rpc_llm.py`**
- `async rpc_complete(rpc_reader, request_dict, timeout) -> dict` — sends `openclaw/llm_complete`
- `serialize_chat_request(request: ChatRequest) -> dict` — Pydantic → JSON-safe dict
- `deserialize_chat_response(data: dict) -> ChatResponse` — dict → Pydantic

**New: `tests/test_provider_openclaw.py`**
- Test complete() serializes ChatRequest correctly
- Test complete() deserializes ChatResponse correctly
- Test parse_tool_calls extracts tool calls
- Test list_models queries OpenClaw
- Test timeout handling
- Test error propagation

**Modified: `pyproject.toml`**
- Add entry point: `provider-openclaw = amplifier_app_openclaw.modules.provider_openclaw:mount`

### Acceptance Criteria
- [ ] Amplifier can run sessions using OpenClaw as the LLM provider
- [ ] Tool calls work through the proxy
- [ ] Token usage properly tracked
- [ ] Model listing works
- [ ] Passes amplifier-core's provider validation tests

---

## Implementation Sequence (Files in Order)

1. `src/amplifier_app_openclaw/modules/__init__.py` — package
2. `src/amplifier_app_openclaw/modules/tool_openclaw.py` — static tool module
3. `tests/test_module_tool_openclaw.py` — tests
4. Update `session_manager.py` — capability registration
5. Update `pyproject.toml` — entry point
6. `src/amplifier_app_openclaw/discovery.py` — capability discovery
7. `tests/test_discovery.py` — discovery tests
8. `src/amplifier_app_openclaw/modules/tool_openclaw_dynamic.py` — dynamic tools
9. `src/amplifier_app_openclaw/rpc_llm.py` — LLM RPC serialization
10. `src/amplifier_app_openclaw/modules/provider_openclaw.py` — provider module
11. `tests/test_provider_openclaw.py` — provider tests
12. Update `pyproject.toml` — provider entry point
13. Integration test with real OpenClaw + Amplifier

## Total Estimate
- ~12 new/modified files
- ~40-50 new tests
- Task 2.2: straightforward (1 agent run)
- Task 2.3: moderate (1 agent run)  
- Task 2.1: complex (1-2 agent runs, serialization is tricky)
