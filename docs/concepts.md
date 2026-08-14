# Concepts

Use this page when you want to understand atom before changing advanced settings. It explains the moving parts without requiring you to read the source first.

If you want source-file ownership and extension points, read [`architecture.md`](./architecture.md) after this page.

## Runtime Shape

atom has one small core loop and several ways to enter it:

| Part | What it does |
|---|---|
| Agent loop | Builds context, selects the session, calls the provider, runs tools, and publishes replies |
| Providers | LLM backends: Anthropic, OpenAI, Groq, and any other OpenAI-compatible API via `custom` |
| Channels | User-facing transports: the CLI, Telegram, and any channel package you add |
| Tools | Capabilities the model may call, including files, shell, web search/fetch, MCP, cron, image generation, and subagents |
| Memory | Workspace files and session history that keep useful context across turns |
| Gateway | Long-running process that connects enabled channels and serves the health endpoint |

The simplest path is `atom agent -m "Hello!"`: one inbound message goes through the agent loop and prints the reply in your terminal. The long-running path is `atom gateway`: channels receive messages from chat apps or API clients, publish them to the same agent loop, and send replies back to the originating channel.

## Config vs Workspace

The default instance lives under `~/.atom/`:

| Path | Meaning |
|---|---|
| `~/.atom/config.json` | Instance configuration: providers, model defaults, channels, tools, gateway, API, and runtime options |
| `~/.atom/workspace/` | Agent workspace: memory, sessions, heartbeat tasks, cron jobs, skills, and generated artifacts |

You can override both with command flags:

```bash
atom onboard --config ./bot-a/config.json --workspace ./bot-a/workspace
atom agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
atom gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
```

The config file controls what atom may use. The workspace is where atom keeps state for that instance.

### Agent Workspace and Project Workspace

The configured workspace is the **agent workspace**. A session can also select
a different **project workspace** for repository-specific work without moving the
agent's identity or durable state.

| Resource | Owner when a project is selected |
|---|---|
| Project instructions | `AGENTS.md` from the selected project; there is no fallback to the agent workspace's `AGENTS.md` |
| Agent profile | `SOUL.md` and `USER.md` from the agent workspace; project-local files with those names are ignored |
| Memory and custom skills | `memory/` and `skills/` from the agent workspace |
| Relative file paths and shell working directory | The selected project workspace |

When no separate project is selected, one directory normally serves both roles.
Selecting a project changes the working context for that chat; it does not create
a second agent or relocate the configured agent workspace.

## Config Format

`config.json` accepts both camelCase and snake_case keys. The docs use camelCase because atom writes config back to disk with camelCase aliases, for example `apiKey`, `modelPresets`, `intervalS`, and `maxToolResultChars`.

Most examples are partial snippets. Merge them into the existing file created by `atom onboard`; do not replace the whole file unless you want to reset the instance.

## One Agent Turn

A normal turn follows this flow:

1. A channel receives a user message and publishes it to the message bus.
2. The agent loop chooses a session key and builds context from the effective project workspace, agent-owned profile/skills/memory, recent messages, channel metadata, and runtime settings.
3. The provider receives the model request.
4. If the model asks for tools, the runner executes them and feeds results back to the model.
5. The final reply is saved to the session and sent back through the channel.

That flow is the same whether the message starts in the CLI, Telegram, or another channel.

## CLI, Gateway, and APIs

| Entry point | Command | Use it for |
|---|---|---|
| CLI one-shot | `atom agent -m "..."` | First-run checks, scripts, and quick local questions |
| CLI interactive | `atom agent` | Terminal chat with persistent session history |
| Gateway | `atom gateway` | Chat apps, heartbeat, Dream, and long-running service mode |
| OpenAI-compatible API | `atom serve` | Programmatic access through `/v1/chat/completions`, including streaming |

The gateway keeps chat channels and other long-running services alive. Its only HTTP route is the health endpoint on `gateway.port` (`18790` by default). Programmatic access is a separate process: `atom serve` on `api.port` (`8900` by default). See [`openai-api.md`](./openai-api.md) for the request and response format.

## Provider and Model Selection

The active model should normally come from a named `modelPresets` entry selected by `agents.defaults.modelPreset`. Direct `agents.defaults.provider` and `agents.defaults.model` still form the implicit `default` preset for older or minimal configs. The active provider is resolved in this order:

1. If the active preset provider or implicit default provider is not `"auto"`, atom uses that provider.
2. If provider is `"auto"`, atom tries to infer the provider from the model name, configured API keys, or configured base URLs.
3. Custom and named custom providers should be pinned explicitly, since a generic model name carries no provider keyword.

Pin the provider inside the preset when setting up for the first time. It is easier to debug:

```json
{
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-opus-4-5"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

See [`providers.md`](./providers.md) for practical examples and [`configuration.md#providers`](./configuration.md#providers) for the full provider reference.

## Channels and Sessions

Each channel maps inbound messages to a session key. That lets independent conversations keep separate history.

`agents.defaults.unifiedSession` can intentionally share one session across channels for a single-user multi-device setup. Leave it off if you expect separate people, groups, channels, or projects to keep separate context.

## Memory, Sessions, and Dream

atom uses two related stores:

| Store | Location | Purpose |
|---|---|---|
| Sessions | `<workspace>/sessions/*.jsonl` | Recent conversation turns replayed into context |
| Memory | `<workspace>/memory/MEMORY.md` and `<workspace>/memory/history.jsonl` | Long-term facts and consolidated history |

Dream is a periodic consolidation job. It reads accumulated history and updates workspace memory so useful context can survive beyond short session replay.

See [`memory.md`](./memory.md) for the detailed design.

## Apps and Agent Plugins

Agent Plugins are atom's common package and activation boundary for
installable capabilities. They organize existing extension types instead of
replacing them:

| Part | Role |
|---|---|
| Agent Plugin | Installable package that can bundle skills, MCP servers, or both |
| Skill | Workflow guidance loaded progressively or invoked with `$skill-name` |
| MCP server | Runtime tools exposed to the agent |
| CLI App | Locally managed executable whose adapter is packaged and activated like a plugin |

Native providers, channels, built-in tools, standalone workspace skills, and
directly configured MCP servers keep their existing extension paths. See
[`configuration.md#agent-plugins-v1`](./configuration.md#agent-plugins-v1) for
the package contract.

## Tools and Safety

Tools are discovered automatically from built-in modules and plugin entry points. Common tool groups include:

- file read/write/edit and patching;
- shell execution with configurable sandboxing;
- web search and web fetch with SSRF checks;
- MCP servers;
- cron reminders, local triggers, and heartbeat tasks;
- image generation;
- subagents and runtime self-inspection.

Security-sensitive controls live in [`configuration.md#security`](./configuration.md#security). For production or shared chat apps, also configure channel access controls such as `allowFrom` or pairing, and set `api.apiKey` before binding `atom serve` beyond loopback.

## Background Jobs

When `atom gateway` starts, it runs workspace-scoped automations and
registers system jobs:

- `dream`, when `agents.defaults.dream.enabled` is true;
- `heartbeat`, when `gateway.heartbeat.enabled` is true.

Heartbeat reads `<workspace>/HEARTBEAT.md`. If the file has tasks under `## Active Tasks`, atom executes them and sends only useful/actionable results to the most recently active chat target. Routine "nothing changed" results are suppressed.

User-created reminders use the same cron service but are not the same as the
protected heartbeat system job. They run as scheduled turns in their origin
chat/session and normally deliver the result back to that channel.

Local triggers are also session-bound, but they do not have their own
schedule. Create one from the target chat with `/trigger <name>`, then call
`atom trigger <id> "<message>"` when a local script or external service wants
atom to respond in that session. Webhook servers, third-party auth, and
event-to-message formatting stay outside atom. Trigger deliveries are stored
in the workspace until the linked agent turn finishes successfully. If the
target session is busy, the trigger waits until that session is idle instead of
being injected into the active turn. The message is recorded as an automation
turn in that session. Delivery is at-least-once, so external systems should
tolerate repeated trigger messages; a delivery that reaches the agent but fails
is marked failed rather than retried forever.

## Where to Go Next

| Need | Read |
|---|---|
| First working install | [`quick-start.md`](./quick-start.md) |
| Provider/model setup | [`providers.md`](./providers.md) |
| Chat app setup | [`chat-apps.md`](./chat-apps.md) |
| Complete config reference | [`configuration.md`](./configuration.md) |
| Runtime debugging | [`troubleshooting.md`](./troubleshooting.md) |
