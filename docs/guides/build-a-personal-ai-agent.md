# How to Build a Personal AI Agent with atom

This guide builds a personal AI agent you can run locally, talk to from the
terminal, and later connect to chat apps, memory, tools, and automations.

## What you will build

- a configured atom install
- one working model provider
- one local agent reply
- a running gateway for ongoing work

## When to use this

Use this when you want a personal AI agent that you control rather than a hosted
chat-only interface. atom is useful when the agent needs local workspace
access, tool calls, session history, memory, scheduled work, or chat app
delivery.

## Install

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
```

The wizard creates `~/.atom/config.json` and helps you choose a provider and
model. If terminals and config files are new to you, use
[Start Without Technical Background](../start-without-technical-background.md)
instead.

## Minimal working example

First prove the runtime can answer:

```bash
atom agent -m "Hello!"
```

Then start the gateway so chat apps and automations stay online:

```bash
atom gateway
```

## Production notes

- Keep one workspace per project or personal context.
- Use `modelPresets` when you want stable names for fast, deep, local, or
  fallback models.
- Keep `atom gateway` running for chat apps and automations.
- Use the Python SDK or OpenAI-compatible API when another program should call
  the agent.

## Security notes

- Do not store API keys directly in shared files; use environment variables.
- Prefer chat app pairing for first setup. Use `allowFrom` only for static
  allowlists, and keep those lists narrow.
- Enable workspace restriction before exposing file or shell tools to other
  users.
- Use a separate workspace for experiments that can modify files.

## Troubleshooting

- `atom status` shows the config path, workspace path, and active model.
- If `atom agent -m "Hello!"` fails, fix provider setup before adding chat
  apps.
- If the CLI answers but a chat app does not, check gateway logs and channel
  credentials.

## Related atom docs

- [Quick Start](../quick-start.md)
- [Concepts](../concepts.md)
- [Configuration](../configuration.md)
- [Troubleshooting](../troubleshooting.md)
