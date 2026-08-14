# CLI Reference

Use this page when you know what you want to run and need the command shape. For a guided first run, start with [`quick-start.md`](./quick-start.md).

## Choose a Command

| Goal | Command | Notes |
|---|---|---|
| Check the install | `atom --version` | If this fails, try `python -m atom --version` |
| Create or refresh config | `atom onboard` | Creates `~/.atom/config.json` and `~/.atom/workspace/` |
| Refresh config non-interactively | `atom onboard --refresh` | Preserves existing values and adds missing default fields without prompting |
| Use guided setup | `atom onboard --wizard` | Best when you prefer prompts over hand-editing JSON |
| Check readiness without calling a model | `atom status` | Summarizes config/workspace and validates the active provider/model configuration |
| Send one test message | `atom agent -m "Hello!"` | First proof that install, config, provider, model, and workspace all work |
| Chat in the terminal | `atom agent` | Interactive local chat; exit with `exit`, `/exit`, `:q`, or `Ctrl+D` |
| Run the gateway | `atom gateway` | Starts chat apps, cron, and heartbeat |
| Deliver a local trigger | `atom trigger <id> "message"` | Created first with `/trigger <name>` in the target chat/session |
| Serve an OpenAI-compatible API | `atom serve` | Starts `/v1/chat/completions`, `/v1/models`, and `/health` |
| Check chat channel setup | `atom channels status` | Useful before starting `atom gateway` |
| Manage optional features | `atom plugins list` | Shows channels and optional capabilities you can turn on |
| Log in to QR/OAuth-style channels | `atom channels login <channel>` | For channel packages that implement an interactive login; Telegram uses a bot token instead |

## Global

```bash
atom --help
atom --version
python -m atom --help
python -m atom --version
```

`python -m atom ...` is useful when the package is installed but the `atom` script is not on `PATH`.

## Common Patterns

Most day-to-day commands use the default config and workspace. Advanced or multi-instance runs usually pass both paths explicitly:

```bash
atom agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
atom gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
atom serve --config ./bot-a/config.json --workspace ./bot-a/workspace
```

Use `--verbose` on long-running processes when you need startup or runtime logs:

```bash
atom gateway --verbose
atom serve --verbose
```

Long-running commands keep working until you stop them. Press `Ctrl+C` in that terminal
to stop foreground `atom gateway` or `atom serve`. If you started the gateway
with `--background`, use `atom gateway stop`.

## Setup

| Command | Description |
|---|---|
| `atom onboard` | Initialize or refresh the default config and workspace |
| `atom onboard --refresh` | Refresh an existing config without prompting, preserving existing values |
| `atom onboard --wizard` | Use the interactive setup wizard |
| `atom onboard --config <path> --workspace <path>` | Initialize or refresh a specific instance |

Default paths:

| Path | Default |
|---|---|
| Config | `~/.atom/config.json` |
| Workspace | `~/.atom/workspace/` |

## Status

| Command | Description |
|---|---|
| `atom status` | Summarize the default config/workspace and check Agent provider/model readiness |
| `atom status --config <path>` | Check a specific config file |
| `atom status --workspace <path>` | Show status with a workspace override |

Status does not send a model request. On success, run the printed
`atom agent -m "Hello!"` command to verify network access and credentials. On failure,
follow the printed `atom onboard --wizard` route.

## Agent CLI

| Command | Description |
|---|---|
| `atom agent -m "Hello!"` | Send one message and exit |
| `atom agent` | Start interactive terminal chat |
| `atom agent --session <id>` | Use a specific session key |
| `atom agent --workspace <path>` | Override workspace |
| `atom agent --config <path>` | Use a specific config file |
| `atom agent --no-markdown` | Print plain text instead of Rich-rendered Markdown |
| `atom agent --logs` | Show runtime logs while chatting |

In interactive mode, `Enter` sends the current message. Press `Alt+Enter` to add a newline before sending.

Interactive mode exits with `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

## Gateway

`atom gateway` starts enabled chat channels, cron-backed system jobs, Dream, heartbeat, and the health endpoint. It is the way to run atom as a long-lived service. By default it runs in the foreground, which keeps existing scripts and terminal workflows unchanged. Use `--background` when you want a local macOS or Linux process that you can manage from the CLI.

| Command | Description |
|---|---|
| `atom gateway` | Start the gateway in the foreground with config defaults |
| `atom gateway --verbose` | Show verbose runtime output |
| `atom gateway --port <port>` | Override `gateway.port` for the health endpoint |
| `atom gateway --workspace <path>` | Override workspace |
| `atom gateway --config <path>` | Use a specific config file |
| `atom gateway --background` | Start the gateway as a background process |
| `atom gateway status` | Show the recorded background gateway PID, state file, and log file |
| `atom gateway logs --no-follow` | Print recent background gateway logs and exit |
| `atom gateway logs` | Follow background gateway logs |
| `atom gateway restart` | Restart the recorded background gateway with the current config |
| `atom gateway stop` | Stop the recorded background gateway |
| `atom gateway install-service` | Install a systemd user service or macOS LaunchAgent |
| `atom gateway install-service --dry-run` | Preview the generated service file and system commands |
| `atom gateway uninstall-service` | Remove the installed system service |

For custom instances, pass the same selector flags to management commands:

```bash
atom gateway --background --config ./bot-a/config.json --workspace ./bot-a/workspace
atom gateway status --config ./bot-a/config.json --workspace ./bot-a/workspace
atom gateway stop --config ./bot-a/config.json --workspace ./bot-a/workspace
atom gateway install-service --config ./bot-a/config.json --workspace ./bot-a/workspace --name bot-a
```

`--background` is a lightweight detached process. `install-service` is for
login/startup integration: Linux uses a systemd user service; macOS uses a
LaunchAgent plist. System services run the foreground gateway under the OS
supervisor rather than nesting another background process.

Default health endpoint:

```text
http://127.0.0.1:18790/health
```

`GET /health` is the only route the gateway serves. For programmatic access, run `atom serve` — see [`openai-api.md`](./openai-api.md).

## Local Triggers

`atom trigger` delivers one local message to a trigger that was created from
a chat/session with `/trigger <name>`.

```bash
atom trigger trg_8K4P2Q9X "Review PR #4502"
```

Keep `atom gateway` running so the message can be delivered to the linked
chat/session. The message is recorded as an automation turn in that session,
not as a normal chat message typed by the user.

The command writes to a workspace-local durable queue. If `atom gateway` is
not running yet, the message waits in that workspace. If the target session is
already running a turn, the trigger waits for that session to become idle. If the
gateway exits after claiming a delivery but before the linked turn completes,
the next gateway start requeues that delivery. The queue is at-least-once, not
exactly-once, so the same message can be delivered again after an interrupted
process. If the agent receives the delivery and the turn fails, the delivery is
marked failed instead of retried indefinitely. Each delivery also writes an
audit record under `<workspace>/triggers/runs`. Run one gateway consumer per
workspace; this local queue is not a distributed multi-consumer queue.

Use stdin when another local process generates the message:

```bash
generate-report | atom trigger trg_8K4P2Q9X
```

Options:

| Command | Description |
|---|---|
| `atom trigger <id> "message"` | Deliver one message through a trigger |
| `atom trigger <id>` | Read the message from stdin |
| `atom trigger --config <path> <id> "message"` | Use the workspace from a specific config |
| `atom trigger --workspace <path> <id> "message"` | Use a specific workspace |

Triggers are created and managed from a chat/session with `/trigger`, not through
separate `list`, `revoke`, or `delete` CLI subcommands.

For webhooks or other external systems, run your own small service and have it
call this CLI after it decides what message atom should receive.

See [Automations](./automations.md) for the broader automation model and delivery
behavior.

## OpenAI-Compatible API

| Command | Description |
|---|---|
| `atom serve` | Start `/v1/chat/completions`, `/v1/models`, and `/health` |
| `atom serve --host <host>` | Override API bind host |
| `atom serve --port <port>` | Override API port |
| `atom serve --timeout <seconds>` | Override per-request timeout |
| `atom serve --verbose` | Show runtime logs |
| `atom serve --workspace <path>` | Override workspace |
| `atom serve --config <path>` | Use a specific config file |

Default API endpoint:

```text
http://127.0.0.1:8900
```

Public binds (`0.0.0.0` or `::`) require `api.apiKey`; send it as a Bearer token on API routes.

See [`openai-api.md`](./openai-api.md) for request examples.

## Status

```bash
atom status
```

Shows the config path, workspace path, active model, and provider summary without calling a model.

| Command | Description |
|---|---|
| `atom status` | Inspect the default instance |
| `atom status --config <path>` | Inspect a specific config |
| `atom status --config <path> --workspace <path>` | Inspect a specific config with a workspace override |

## Channels

| Command | Description |
|---|---|
| `atom channels status` | Show configured channel status |
| `atom channels status --config <path>` | Show channel status for a specific config |
| `atom channels login <channel>` | Run interactive login for supported channels |
| `atom channels login <channel> --force` | Re-authenticate even if credentials already exist |
| `atom channels login <channel> --config <path>` | Use a specific config file |
| `atom plugins list --config <path>` | Show plugin/channel enabled state for a specific config |

Examples:

```bash
atom channels status
```

Telegram authenticates with a bot token in `~/.atom/config.json`, so it does
not use `channels login`. The command stays available for [channel
packages](./channel-package-guide.md) that implement an interactive login.

See [`chat-apps.md`](./chat-apps.md) for channel-specific setup.

## Optional Features

Use these commands when you want atom to add or remove a built-in capability
without hand-editing JSON. Enabling may install the support package first.
Disabling applies to channels such as Telegram; it keeps your saved settings and
turns the channel off.

The `plugins` command name is retained for compatibility, but these entries are
atom runtime support packages, not user-invokable agent tools. They cannot be
attached to a chat turn with `@`.

| Feature name | What it enables |
|---|---|
| `api` | Dependencies required by the OpenAI-compatible `atom serve` process |
| `langfuse` | Langfuse tracing support for OpenAI-compatible providers |
| `olostep` | Olostep web search provider support |
| A channel name such as `telegram` | The connector package and saved channel enablement |

| Command | Description |
|---|---|
| `atom plugins list` | Show available channels and optional capabilities |
| `atom plugins enable <name>` | Install missing support and enable the feature or channel |
| `atom plugins enable <name> --logs` | Show package install logs while enabling |
| `atom plugins disable <channel>` | Turn off a channel without deleting its saved settings |
| `atom plugins list --config <path>` | Read a specific config file |
| `atom plugins enable <name> --config <path>` | Update a specific config file |
| `atom plugins disable <channel> --config <path>` | Turn off a channel in a specific config file |

Document and PDF reading are included in the standard installation. The old
`atom plugins enable documents` and `atom plugins enable pdf` commands
remain accepted as no-op compatibility aliases.


## Useful First Checks

```bash
atom --version
atom status
atom agent -m "Hello!"
```

If these fail, use [`troubleshooting.md`](./troubleshooting.md) before debugging chat apps, systemd, or SDK integrations.
