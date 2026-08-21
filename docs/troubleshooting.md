# Troubleshooting

Use this page to isolate where a failure lives. Start with the smallest surface that proves the most: local CLI first, then gateway, then chat apps.

## Fast Diagnosis Order

Run these in order:

```bash
atom --version
atom status
atom agent -m "Hello!"
```

Then, only if the CLI works:

```bash
atom gateway
```

This separates failures into layers:

| Layer | What it proves |
|---|---|
| `atom --version` | Install and shell command discovery |
| `atom status` | Config path, workspace, environment references, and active provider/model configuration |
| `atom agent -m "Hello!"` | Config loading, provider/model access, workspace writes, and agent loop |
| `atom gateway` | Channel startup, cron system jobs, heartbeat, and health endpoint |

If `atom agent -m "Hello!"` fails, fix that before debugging Telegram or systemd.

`atom status` does not call the model. If provider/model setup is incomplete, it points to
the CLI setup wizard, then prints the command to check again.

## How to Read `atom status`

`atom status` does not call a model. It checks the selected config and workspace,
resolves environment references, and validates the local settings required by the active
provider/model without constructing a provider client.

The output has this shape:

```text
atom Status

Config: /path/to/config.json ✓
Workspace: /path/to/workspace ✓
Model: claude-opus-4-5 (preset: primary)
Agent: ✓ provider/model configuration is ready
Custom: not set
Anthropic: ✓
OpenAI: not set
Groq: not set
```

Read it like this:

| Line | Good sign | What to do if it looks wrong |
|---|---|---|
| `Config` | It points to the config file you meant to use and shows `✓`. | Run `atom onboard`, or pass `--config` to `atom agent`, `gateway`, or `serve` when testing a non-default instance. |
| `Workspace` | It points to the workspace you meant to use and shows `✓`. | Run `atom onboard`, create the folder, fix permissions, or pass `--workspace` on commands that support it. |
| `Model` | It shows the active model or the preset name you expect. | Set `agents.defaults.modelPreset` to the intended preset, or check `/model` if you changed models during a chat session. |
| `Agent` | It says `provider/model configuration is ready`. | Follow the printed setup route, then run `atom status` again. |
| Provider rows | The provider used by the active preset shows `✓`. | Configure only the active provider first. It is normal for unused providers to say `not set`. |

If `atom status` looks right but `atom agent -m "Hello!"` fails, the install and config paths are probably fine. Continue with [Provider and Model Problems](#provider-and-model-problems).

## Installation Problems

atom runs on Linux and macOS and is installed with `uv`, which manages its own
Python interpreter — you do not need a matching system Python.

| Symptom | Check |
|---|---|
| `uv: command not found` | Install `uv` with `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then open a new terminal. |
| `atom: command not found` | Run `uv tool update-shell` and open a new terminal, or call it as `uv tool run --from "git+https://github.com/0sage/atom.git" atom ...`. |
| Could not reach `github.com` | Your network, proxy, or firewall blocked the git fetch. Configure the proxy for git, then rerun `uv tool install`. |
| `No solution found` or a build failure | Run `uv python list`; `uv` needs to resolve or download a Python 3.11+ interpreter, which requires network access on first use. |
| An optional feature disappeared after upgrade | A bare `uv tool upgrade` rebuilds the environment from the recorded requirements only. Use `atom upgrade`, which passes enabled channels' requirements back through `--with`; enabled channels also repair themselves on the next gateway start. |
| Behaviour did not change after an upgrade | The gateway is still running the old code: a long-lived process keeps the modules it started with, and `/health` reports `ok` regardless. Run `atom upgrade` (it restarts the service), or restart it yourself. Compare `atom --version` against the `Starting atom gateway version ...` line in the service log. |
| Source checkout does not pick up edits | From the repo root, run `uv sync --all-extras --dev`, then check `uv run --no-sync atom --version`. |

## Config Problems

Default config path:

```text
~/.atom/config.json
```

Default workspace path:

```text
~/.atom/workspace/
```

`atom status` reads the default config unless you pass explicit paths. Use the same `--config` and `--workspace` across status checks and runtime commands when debugging multiple instances:

```bash
atom status --config ./bot-a/config.json --workspace ./bot-a/workspace
atom agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
atom gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
```

Common config mistakes:

| Symptom | Check |
|---|---|
| JSON parse error | Validate commas, braces, and quotes. Most docs examples are partial snippets to merge. |
| Unknown or missing provider | Use a provider registry name (`anthropic`, `openai`, `openai_codex`, `groq`, `custom`), or define your own OpenAI-compatible provider key under `providers` and reference that exact key from the active preset. |
| snake_case vs camelCase confusion | Both are accepted, but docs use camelCase because atom writes config with aliases such as `apiKey`, `modelPresets`, `intervalS`. |
| Environment variable error | `${VAR_NAME}` references are resolved at startup. Set the variable before running atom. |
| Edited config but behavior did not change | Restart `atom gateway`; long-running processes read config at startup. |

After editing config, check the shortest path to an Agent reply:

```bash
atom status
```

To refresh missing defaults without overwriting existing settings, run:

```bash
atom onboard --refresh
```

For an interactive choice between resetting and refreshing, run `atom onboard` and choose the option that keeps current values and merges missing defaults.

## Provider and Model Problems

First prove the provider in the CLI:

```bash
atom agent -m "Hello!"
```

Then compare your config against [`providers.md`](./providers.md).

If you need a known-good snippet instead of diagnosis, use [`provider-cookbook.md`](./provider-cookbook.md).

| Symptom | Likely cause |
|---|---|
| 401, unauthorized, invalid API key | Key is missing, expired, pasted with whitespace, or under the wrong provider key. |
| Model not found | The model ID belongs to a different provider. |
| Provider cannot be inferred | Pin `modelPresets.<name>.provider` in the active preset instead of using `"auto"`. For legacy direct configs, pin `agents.defaults.provider`. |
| Local model connection refused | The local server (Ollama, vLLM, LM Studio, or similar behind `custom`) is not running, or `apiBase` points to the wrong port. |

## Langfuse Problems

Langfuse tracing is optional and controlled by environment variables.

| Symptom | Check |
|---|---|
| `LANGFUSE_SECRET_KEY is set but langfuse is not installed` | Install `langfuse` in the same Python environment that runs atom, then restart the process. |
| No traces appear | Set `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_BASE_URL` before starting atom. |
| Wrong Langfuse project or region | Check that the key pair and `LANGFUSE_BASE_URL` come from the same Langfuse project/region. |
| Only some providers trace | Langfuse tracing applies to OpenAI-compatible provider calls; native providers may not use that client path. |

See [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) for setup commands.

## Gateway Problems

`atom gateway` is required for chat apps, heartbeat, Dream, and long-running channel connections.

Default ports:

| Surface | Default |
|---|---|
| Gateway health endpoint | `http://127.0.0.1:18790/health` |
| OpenAI-compatible API (`atom serve`) | `http://127.0.0.1:8900` |

Common gateway checks:

```bash
atom gateway --verbose
```

| Symptom | Check |
|---|---|
| Port already in use | Change `gateway.port`, or the `--port` CLI flag for the relevant command. |
| A request to `18790` returned 404 | `GET /health` is the only route the gateway serves. For programmatic access, run `atom serve` and use its OpenAI-compatible API. |
| Config changes ignored | Restart the gateway. |
| Startup pauses at `Installing optional feature` | An enabled channel is missing its Python dependencies. See [Slow Optional Channel Dependency Installation](#slow-optional-channel-dependency-installation). |
| Heartbeat never runs | Keep the gateway running, add tasks under `<workspace>/HEARTBEAT.md` -> `## Active Tasks`, and make sure `gateway.heartbeat.enabled` is true. |
| Cron jobs disappeared after switching workspaces | Cron jobs are workspace-scoped at `<workspace>/cron/jobs.json`; check you are using the intended workspace. |

### Slow Optional Channel Dependency Installation

Before loading enabled channels, the gateway checks the dependencies declared by their
channel manifests. `atom plugins enable <channel>` normally installs these dependencies when a
channel is enabled. Installation during startup is a recovery path for an enabled config whose Python
environment no longer has the required packages, for example after manually editing the
config, upgrading atom, or recreating the isolated `uv tool` environment. The
gateway waits for the install so an enabled channel is not silently skipped; later starts
skip the installation once the dependencies are present.

If access to PyPI is slow in your region, configure pip to use a trusted package index. The
installer honors the standard `PIP_INDEX_URL` environment variable, including when atom
itself was installed with `uv tool`:

```bash
PIP_INDEX_URL=https://your-trusted-mirror.example/simple atom gateway
```

For the systemd user service created by `atom gateway install-service`, add a drop-in:

```bash
systemctl --user edit atom-gateway.service
```

```ini
[Service]
Environment="PIP_INDEX_URL=https://your-trusted-mirror.example/simple"
```

Then reload and restart the service:

```bash
systemctl --user daemon-reload
systemctl --user restart atom-gateway.service
```

For a system-level or custom service, use `sudo systemctl edit <unit>` instead. Prefer an
HTTPS index operated by an organization you trust, and do not put index credentials in
commands or logs.

## Chat App Problems

Before debugging a chat app:

```bash
atom agent -m "Hello!"
atom channels status
atom gateway
```

Then check:

| Symptom | Check |
|---|---|
| Bot never replies | Gateway is not running, the channel is not enabled, or the bot/app token is wrong. |
| Unknown sender ignored | Configure `allowFrom`, pairing, or the channel-specific allow list. |
| Telegram has a saved token but cannot complete a live check | The token is still valid. Confirm the gateway can reach `api.telegram.org`, or set `channels.telegram.proxy` to an HTTP or SOCKS proxy. |
| Telegram rejects the token | Copy the current token from BotFather or regenerate it. |
| Telegram receives no messages | Confirm the channel is enabled, the gateway is running, and the sender is paired or listed in `allowFrom`. |
| Telegram group messages ignored | `groupPolicy` defaults to `mention`, so the bot only answers when mentioned. Set it to `open` to answer every group message. |
| Chat app works but an API client does not | The provider and agent are likely fine; `atom serve` is a separate process from the gateway, so check its host, port, and `api.apiKey` with [`openai-api.md`](./openai-api.md). |

See [`chat-apps.md`](./chat-apps.md) for channel-specific setup.

## Tool and Workspace Problems

| Symptom | Check |
|---|---|
| File access denied | Check `tools.restrictToWorkspace` and whether the target path is inside the active workspace. |
| Web fetch blocked | SSRF protection blocks unsafe targets; use `tools.ssrfWhitelist` only for trusted private networks. |
| MCP tools missing | Check `tools.mcpServers`, server startup command, environment variables, and tool allow list. |
| Generated artifacts are missing | Check the active workspace and channel media directory. |

## Memory and Session Problems

| Symptom | Check |
|---|---|
| Conversation context seems wrong | Confirm the active workspace and session. API clients and chat app threads may use different sessions. |
| Memory does not update immediately | Dream consolidation is periodic; recent turns still live in session history. |
| Old sessions appear after moving config | Session files are stored under `<workspace>/sessions/`; verify the workspace path. |
| You want one shared session across devices | Set `agents.defaults.unifiedSession` intentionally; otherwise keep separate sessions. |

## Collect Useful Evidence

When opening an issue or asking for help, include:

- install method and `atom --version`;
- operating system and Python version;
- the command you ran;
- relevant `atom status` output;
- sanitized config snippets, especially provider, model, channel, and tool settings;
- gateway logs from `atom gateway --verbose`;
- whether `atom agent -m "Hello!"` works.

Never paste real API keys, bot tokens, OAuth tokens, or private chat IDs into public issues.

If you find a docs mistake, outdated command, or confusing step, fix the page in this repository.
