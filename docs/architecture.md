# Architecture

This page maps atom's runtime behavior to source files. Use it when you are debugging internals, reviewing a PR, adding a provider/channel/tool, or trying to understand where a user-visible behavior comes from.

For the product-level mental model, read [`concepts.md`](./concepts.md) first.

## Core Flow

```mermaid
flowchart LR
    Channel["Channel<br/>CLI, chat apps, HTTP API"] --> Bus["MessageBus<br/>InboundMessage"]
    Bus --> Loop["AgentLoop<br/>session, workspace, context"]
    Loop --> Runner["AgentRunner<br/>provider/tool loop"]
    Runner --> Provider["Provider<br/>LLM backend"]
    Provider --> Runner
    Runner --> Tools["Tools<br/>files, shell, web, MCP, cron"]
    Tools --> Runner
    Runner --> Loop
    Loop --> Outbound["MessageBus<br/>OutboundMessage"]
    Outbound --> Channel

    Loop -. reads/writes .-> State["Session, memory,<br/>hooks, skills, templates"]
```

Main files:

| Area | Files |
|---|---|
| Message events and queue | `atom/bus/events.py`, `atom/bus/queue.py` |
| Turn orchestration | `atom/agent/loop.py` |
| Provider/tool conversation loop | `atom/agent/runner.py` |
| Context construction | `atom/agent/context.py` |
| Session storage and compaction | `atom/session/manager.py` |
| Long-term memory and Dream | `atom/agent/memory.py` |

## Agent Loop vs Agent Runner

`AgentLoop` owns the channel-facing turn:

- receives inbound messages;
- determines the effective session and workspace scope;
- builds context;
- wires hooks, progress, and channel metadata;
- publishes outbound messages.

`AgentRunner` owns the model-facing loop:

- sends messages to the selected provider;
- handles streaming deltas and reasoning blocks;
- executes tool calls;
- feeds tool results back into the model;
- stops when a final answer is produced or runtime limits are hit.

MCP connections are application-owned infrastructure. Composition roots create
an `MCPProvider`, share its `ToolRegistry` with `AgentLoop`, await `connect()`
before use, and guarantee `aclose()` during shutdown; the loop does not manage
that lifecycle. `AgentLoop.from_config()` therefore requires a caller-owned
`ToolRegistry`; callers using MCP share it with their application-owned
`MCPProvider`.

Keep this split in mind when debugging. If a problem is about channel routing, session keys, workspace selection, or outbound delivery, start in `agent/loop.py`. If it is about provider calls, tool calls, streaming, or iteration limits, start in `agent/runner.py`.

## Providers

Provider metadata is centralized in `atom/providers/registry.py`. Configuration fields live in `atom/config/schema.py`.

Provider selection uses:

- explicit `agents.defaults.provider` or preset provider;
- provider registry keywords;
- API key prefixes and API base URL hints;
- local provider fallback when `apiBase` is configured;
- gateway fallback for providers that can route many model families.

Provider implementations live in `atom/providers/`. OpenAI, Groq, and custom endpoints use the OpenAI-compatible implementation, while Anthropic has a specialized native path.

Useful docs:

- [`providers.md`](./providers.md) for practical setup;
- [`configuration.md#providers`](./configuration.md#providers) for exact provider reference.

## Channels

Channels translate external platforms into `InboundMessage` events and send `OutboundMessage` events back to the platform.

Main files:

| Area | Files |
|---|---|
| Base channel contract | `atom/channels/base.py` |
| Channel packages | `atom/channels/<channel>/` |
| Discovery and lifecycle | `atom/channels/manager.py` |

Channels are discovered by scanning self-contained packages under `atom/channels/`. Add a channel by contributing one package that follows [`channel-package-guide.md`](./channel-package-guide.md).

## Gateway

`atom gateway` starts:

- enabled chat channels;
- workspace-scoped cron service;
- system jobs such as Dream and heartbeat;
- the health endpoint on `gateway.port`.

The health endpoint is the gateway's only HTTP route. Chat channels open outbound
connections to their platforms, so the gateway needs no inbound port for them:

| Surface | Default |
|---|---|
| Health endpoint | `http://127.0.0.1:18790/health` |

It is implemented as a minimal `asyncio.start_server` handler in
`atom/cli/gateway_runtime.py`; anything other than `GET /health` returns 404.
The OpenAI-compatible HTTP API is a separate process (`atom serve`,
`atom/api/server.py`), not part of the gateway.

## Tools

Tools are discovered from `atom/agent/tools/` and plugin entry points.

Important files:

| Tool area | Files |
|---|---|
| Tool base and schema | `atom/agent/tools/base.py`, `atom/agent/tools/schema.py` |
| Discovery | `atom/agent/tools/registry.py` |
| Shell execution | `atom/agent/tools/shell.py` |
| Filesystem tools | `atom/agent/tools/filesystem.py` |
| Web search/fetch | `atom/agent/tools/web.py` |
| MCP tools | `atom/agent/tools/mcp.py` |
| Cron | `atom/agent/tools/cron.py`, `atom/cron/` |
| Image generation | `atom/agent/tools/image_generation.py` |
| Runtime self-inspection | `atom/agent/tools/self.py` |

Tool behavior is part of the model contract. Keep user-visible tool names, schemas, and error messages stable unless a change is intentional.

## Config and Paths

The config schema lives in `atom/config/schema.py`. Loading and saving live in `atom/config/loader.py`. Runtime path helpers live in `atom/config/paths.py`.

Defaults:

| Path | Default |
|---|---|
| Config | `~/.atom/config.json` |
| Workspace | `~/.atom/workspace/` |
| Sessions | `<workspace>/sessions/*.jsonl` |
| Memory | `<workspace>/memory/` |
| Cron store | `<workspace>/cron/jobs.json` |
| Runtime state, media, and logs | config directory subdirectories: `webui/` (persisted runtime state such as token usage; the name is kept so existing installs do not orphan saved state), `media/`, and `logs/` |

The schema accepts both camelCase and snake_case keys, but saves config with camelCase aliases.

### Agent-Owned State vs Effective Project Context

Runtime code distinguishes the configured agent workspace from the effective
project workspace carried by a session scope. They are often the same path, but
a session may select a separate project:

| Concern | Path owner |
|---|---|
| Sessions, `SOUL.md`, `USER.md`, memory, and custom skills | Configured agent workspace |
| Project `AGENTS.md`, relative tool paths, and shell working directory | Effective project workspace |
| Workspace access mode and project metadata | Session workspace scope |

`ContextBuilder` combines project instructions with agent-owned profile and
memory. Filesystem and search tools use the project as their ordinary boundary
and receive only capability-specific read access to built-in/agent skills and
the exact agent history file. Keep those cross-root capabilities read-only and
explicit; do not treat the entire agent workspace as an allowed root.

## Memory and Sessions

Session history is the near-term conversation replay. Memory is the longer-term workspace state.

| Store | File area |
|---|---|
| Session JSONL files | `<workspace>/sessions/` |
| Long-term memory | `<workspace>/memory/MEMORY.md` |
| Consolidation source history | `<workspace>/memory/history.jsonl` |
| Bootstrap identity files | `<workspace>/SOUL.md`, `<workspace>/USER.md`, templates under `atom/templates/` |

Dream is implemented in `atom/agent/memory.py` and scheduled by the runtime when enabled.

## Security Boundaries

Security-sensitive code paths include:

| Boundary | Files |
|---|---|
| Workspace scope | `atom/security/workspace_access.py`, `atom/security/workspace_policy.py` |
| Shell sandboxing | `atom/agent/tools/shell.py` |
| SSRF/network checks | `atom/security/network.py`, `atom/agent/tools/web.py` |
| PTH guard and CLI startup security | `atom/security/` and CLI entrypoints |
| Channel access control | channel config in `atom/channels/*.py` |

When changing tools, channels, file access, session workspace behavior, or network fetching, treat security as part of the functional behavior and update docs if the user-facing boundary changes.

## Extension Points

| Extension | How |
|---|---|
| Provider | Add `ProviderSpec` in `providers/registry.py`, add schema field in `config/schema.py`, implement provider only if the generic backend is not enough |
| Channel | Export a `ChannelPlugin` descriptor, keep its runtime and optional setup surfaces in one package, and follow [`channel-package-guide.md`](./channel-package-guide.md) |
| Tool | Implement a tool under `agent/tools/` or expose a plugin entry point |
| Agent Plugin | Add a v1 package under `<workspace>/plugins/` and enable it from Apps |
| MCP | Add `tools.mcpServers` config or bundle the server in an Agent Plugin |
| Skill | Add workspace skills under `<workspace>/skills/`, bundle them in an Agent Plugin, or add built-in skills under `atom/skills/` |
| CLI App | Add it to the CLI Apps catalog; the installer owns its executable lifecycle and writes a skills-only Agent Plugin |

Prefer existing registry/discovery patterns over ad hoc wiring.

## Testing and Verification

Common checks:

```bash
pytest tests/test_openai_api.py::test_function -v
ruff check atom/
```

Choose tests based on the changed surface:

| Change | Minimum useful verification |
|---|---|
| Provider behavior | Provider unit tests or a mocked API path; `atom agent -m "Hello!"` with safe config when possible |
| Channel behavior | Channel tests plus `atom gateway` startup path |
| HTTP API behavior | `tests/test_openai_api.py` and `tests/test_api_stream.py`, plus a request against a running `atom serve` |
| Tool behavior | Tool unit tests and an agent-run path when schema or model-facing behavior changes |
| Docs | Link checks, command accuracy against CLI/schema, and `git diff --check` |

For user-facing flows, prefer at least one verification path through the public surface the user actually touches: CLI command, HTTP endpoint, chat channel, or packaged import.
