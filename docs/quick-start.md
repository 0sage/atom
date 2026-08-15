# Install and Quick Start

This guide has one goal: get a normal atom reply in your terminal. Do not add chat apps, MCP servers, fallback models, or deployment until this path works.

If terminals, Python, or API keys are unfamiliar, use the [beginner walkthrough](./start-without-technical-background.md), which explains each term and step.

## What You Need

- Linux or macOS.
- [`uv`](https://docs.astral.sh/uv/) — the only supported installer. It provides a
  suitable Python (3.11+) itself, so the system Python version does not matter.
- Access to one supported AI provider, company endpoint, or local model server.
- The credential, endpoint URL, and model ID required by that service. A local server behind the `custom` provider may not require a key.

Git is only needed for a source checkout.

## 1. Install atom

Install `uv` first if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install atom into its own isolated environment:

```bash
uv tool install "git+https://github.com/0sage/atom.git"
```

atom is not published on PyPI, and the PyPI name `atom` belongs to an unrelated
project — always install from the git URL.

If the shell cannot find `atom` afterwards, run `uv tool update-shell` once and
open a new terminal.

## 2. Configure Your Model

Run the guided setup wizard:

```bash
atom onboard --wizard
```

It asks you to:

1. Choose the provider or endpoint that owns your credential.
2. Enter its API key or base URL when required.
3. Name a model preset using a model ID that provider can run.

The wizard creates or updates:

| Path | Purpose |
|---|---|
| `~/.atom/config.json` | Provider, model, channel, tool, and runtime settings |
| `~/.atom/workspace/` | Sessions, memory, skills, automations, and generated files |

To create the same files without prompts and edit JSON yourself, run `atom onboard` and see [Manual Configuration Fallback](#manual-configuration-fallback).

## 3. Check the Setup

```bash
atom status
```

You want:

- a check mark for **Config** and **Workspace**;
- the model or preset you selected;
- a configured state for the provider used by that model.

Most other providers can say `not set`. This command validates local setup but does not call the model.

## 4. Get the First Reply

Send one message:

```bash
atom agent -m "Hello!"
```

Any normal assistant answer is success. It proves that atom can load the config, reach the selected model, and use the workspace.

Then start an interactive terminal chat with:

```bash
atom agent
```

In interactive mode, `Enter` sends and `Alt+Enter` inserts a newline. Exit with `exit`, `/exit`, `:q`, or `Ctrl+D`.

## 5. Run the Gateway

`atom agent` is a single local session. To run chat channels, automations, and the heartbeat continuously, start the gateway:

```bash
atom gateway
```

Leave that terminal open. For a managed background process, stop it with `Ctrl+C`, then run:

```bash
atom gateway --background
atom gateway status
```

Use `atom gateway logs`, `restart`, and `stop` to manage that background gateway.

## Choose One Next Step

After the first reply works, add one capability and test again:

| Goal | Recommended path |
|---|---|
| Learn sessions, workspaces, tools, and access modes | [Concepts](./concepts.md) |
| Connect a chat platform | [Chat Apps](./chat-apps.md) |
| Change or add a model | [Provider Cookbook](./provider-cookbook.md) |
| Add web search, voice, or image generation | [Configuration](./configuration.md) |
| Add an MCP integration | [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Schedule agent work | [Automations](./automations.md) |
| Run continuously or remotely | [Deployment](./deployment.md) |
| Integrate from code | [Python SDK](./python-sdk.md) or the [OpenAI-Compatible API](./openai-api.md) |

## Other Install Methods

**Pin a version.** Any git ref works after `@` — a branch, a tag, or a commit SHA:

```bash
uv tool install "git+https://github.com/0sage/atom.git@main"
```

**Add optional dependencies.** Declare them with `--with` so `uv tool upgrade`
cannot drop them:

```bash
uv tool install \
  --with "python-telegram-bot[socks,webhooks]>=22.6,<23.0" \
  --with "socksio>=1.0.0,<2.0.0" \
  --with "python-socks[asyncio]>=2.8.0,<3.0.0" \
  "git+https://github.com/0sage/atom.git[api]"
```

**Use it as a library.** For the Python SDK, install into a project environment
instead of as a tool:

```bash
uv pip install "git+https://github.com/0sage/atom.git"
```

**Run without installing:**

```bash
uv tool run --from "git+https://github.com/0sage/atom.git" atom --version
```

**Source checkout** for development:

```bash
git clone https://github.com/0sage/atom.git
cd atom
uv sync --all-extras --dev
uv run --no-sync atom --version
```

## Manual Configuration Fallback

Use this only when the wizard is unavailable or you intentionally manage JSON. First run `atom onboard`, then merge a provider and a named model preset into `~/.atom/config.json`.

A generic OpenAI-compatible setup has this shape:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${PROVIDER_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "model-id-from-your-provider"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Replace the provider, endpoint, and model together. Do not pair a credential from one service with a model ID from another. See [Provider Cookbook](./provider-cookbook.md) for hosted, OAuth, company, and local examples, and [Configuration](./configuration.md) for exact fields.

## Updating

```bash
atom upgrade
```

This bumps the install and then restarts the gateway. The restart matters: a
running gateway keeps the code it started with, so new files alone change
nothing about the process answering messages, and its health check keeps
reporting `ok` regardless. `uv tool upgrade atom` does the first half only.

An unpinned git URL tracks the default branch, so an upgrade rebuilds whatever
`main` currently points at; pass `--ref v0.10.1` to pin a tag instead. Anything
installed at run time — `atom plugins enable ...`, or channel dependencies the
gateway installs on first start — is not recorded in the tool receipt and a bare
`uv tool upgrade` drops it; `atom upgrade` passes enabled channels' requirements
back through `--with` so they survive.

For a source checkout:

```bash
git pull
uv sync --all-extras --dev
```

Then check `atom --version`. Run `atom onboard --refresh` when you want to add newly introduced default fields while preserving existing settings.

## If the First Reply Fails

Do not change several settings at once. Start with:

```bash
atom --version
atom status
atom agent -m "Hello!"
```

| Symptom | First check |
|---|---|
| `atom: command not found` | Run `uv tool update-shell` and open a new terminal, or call it as `uv tool run --from "git+https://github.com/0sage/atom.git" atom ...` |
| JSON parse error | Check commas and braces; remember that docs examples are usually snippets |
| `401` or invalid API key | Verify the selected provider owns that key and remove accidental spaces |
| Model not found | Use a model ID available from the provider selected in the active preset |
| CLI works but a chat app does not | Run `atom channels status`, then check the channel's config block |

Continue with the ordered [Troubleshooting guide](./troubleshooting.md) if the cause is still unclear.
