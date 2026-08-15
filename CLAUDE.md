This file provides guidance to AI coding agents working with this repository.

## Response Style

Answer in the fewest words that fully answer the question. These are rules, not
preferences.

- A yes/no question gets `Yes.` or `No.` as the first word. Add at most one
  sentence, and only if the answer is conditional.
- Default to 4 lines of prose or fewer. Go longer only when asked to explain, or
  when reporting a plan or a design trade-off.
- No preamble and no summary. Skip "I'll check...", "Let me...", "In summary".
- State the finding, not the search. `Yes — the hook is at
  context_governance.py:123` beats a walkthrough of how that was verified.
- Cite `file.py:line` instead of pasting code the reader can open.
- Do not volunteer caveats, alternatives, or unrelated observations. Mention a
  limitation only when it changes what the reader should do next.
- Verbosity is not thoroughness: verify as deeply as the task needs, then report
  briefly.

## Project Overview

atom is a lightweight, open-source AI agent framework written in Python. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

## Development Commands

```bash
# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check atom/

# Strict type checking (run before every commit)
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright

# Version (never hand-edit pyproject.toml's version)
python -m scripts.bump_version --check
python -m scripts.bump_version patch|minor|major

# Gateway
atom gateway
```

## Releasing

Every pushed commit carries a version bump, made in that same commit, so
`atom --version` always identifies exactly one tree. `X.Y.Z`, one component per
release, always by one: `major` breaks a working setup, `minor` adds capability
that keeps it working, `patch` makes existing behavior more correct. Changes an
operator cannot see (tests, `.agent/` docs, comments) are still `patch`.

Never hand-edit the version; `scripts/bump_version.py` is the only writer and it
rejects multi-step jumps and backwards moves. Push with `--follow-tags` so the
tag never lags the commit. Full rules, the ambiguous cases, and the release
sequence: [`.agent/versioning.md`](.agent/versioning.md).

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`atom/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`atom/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`atom/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`atom/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`atom/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`atom/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API) built on a common base (`base.py`). Includes image generation (`image_generation.py`) and audio transcription (`transcription.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`atom/channels/`): Platform integrations. Telegram is the only channel that ships in-tree. `manager.py` discovers and coordinates them. Channels are self-contained packages auto-discovered via `pkgutil` scanning, so additional ones can be added out-of-tree.
- **Tools** (`atom/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), shell execution (with sandbox backends), web search/fetch, MCP servers, cron, notebook editing, subagent spawning, image generation, and self-modification. Tools are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`atom/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`atom/session/`): Per-session history, context compaction, and TTL-based auto-compaction (`manager.py`).
- **Config** (`atom/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.atom/config.json`. Supports camelCase aliases for JSON compatibility.
- **API Server** (`atom/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`) for programmatic access, started by `atom serve`. This is the only HTTP surface for clients; the gateway itself serves just `GET /health`.
- **Command Router** (`atom/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`atom/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed).
- **Pairing** (`atom/pairing/`): DM sender approval store with persistent pairing codes per channel.
- **Skills** (`atom/skills/`): Built-in skill definitions (cron, github, image-generation, etc.) loaded into agent context.
- **Security** (`atom/security/`): PTH file guard and other security measures activated at CLI entry.

### Entry Points

- **CLI**: `atom/cli/commands.py`
- **Python SDK**: `atom/atom.py`

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)

The security boundaries are imported rather than linked, so they are always in
context. A link loads only if the agent chooses to follow it, and these describe
guards that must not be bypassed — the one file where "usually read" is not good
enough. The other two stay links to keep this file cheap to load every turn.

@.agent/security.md

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.

## Common File Locations

- Config schema: `atom/config/schema.py`
- Provider base / new provider template: `atom/providers/base.py`
- Channel base / new channel template: `atom/channels/base.py`
- Tool registry: `atom/agent/tools/registry.py`
- Tests mirror the `atom/` package structure.
