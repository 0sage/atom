<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./images/readme-cover-dark.svg">
  <img alt="atom README cover" src="./images/readme-cover-light.svg">
</picture>

# atom

**atom** is a self-hosted personal AI agent runtime written in Python. It runs in
your terminal or in Telegram, and keeps a small readable core: an agent loop, a
provider layer, tools, session memory, and a gateway process.

Linux and macOS, Python 3.11+. Providers: `anthropic`, `openai`, `groq`, and any
OpenAI-compatible endpoint via `custom`. A trimmed fork of
[HKUDS/nanobot](https://github.com/HKUDS/nanobot), [MIT](./LICENSE) licensed.

## Install

atom is not published on PyPI, and the PyPI name `atom` belongs to an unrelated
project — always install from the git URL.

```bash
curl -LsSf https://github.com/0sage/atom/raw/main/scripts/install.sh | sh
atom onboard --wizard
atom agent -m "Hello!"
```

The installer adds the prerequisites, [`uv`](https://docs.astral.sh/uv/), and
atom, then stops: it writes no config and starts no service. Flags go after
`-s --` (`--check` to change nothing, `--ref v0.8.2` to pin). Already have `uv`?
`uv tool install "git+https://github.com/0sage/atom.git"`.

Upgrade with `atom upgrade`. It bumps the install *and* restarts the gateway,
which `uv tool upgrade atom` cannot do — a running gateway keeps the code it
started with, and goes on reporting healthy while it does.

## Docs

| Need | Read |
|---|---|
| First reply in the terminal | [Quick Start](./docs/quick-start.md) |
| No terminal background | [Start Without Technical Background](./docs/start-without-technical-background.md) |
| Providers and models | [Providers](./docs/providers.md), [Provider Cookbook](./docs/provider-cookbook.md) |
| Every config field | [Configuration](./docs/configuration.md) |
| Telegram setup | [Chat Apps](./docs/chat-apps.md) |
| Keep it running | [Deployment](./docs/deployment.md) |
| Cron and triggers | [Automations](./docs/automations.md) |
| Memory and Dream | [Memory](./docs/memory.md) |
| Slash commands | [Chat Commands](./docs/chat-commands.md) |
| Programmatic use | [Python SDK](./docs/python-sdk.md), [OpenAI-compatible API](./docs/openai-api.md) |
| Something is broken | [Troubleshooting](./docs/troubleshooting.md) |
| Internals and extension points | [Architecture](./docs/architecture.md), [Development](./docs/development.md) |

Full index: [docs/README.md](./docs/README.md).
