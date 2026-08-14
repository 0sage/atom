# Multiple Instances

Run multiple atom instances simultaneously with separate configs and runtime data. Use `--config` as the main entrypoint. Optionally pass `--workspace` during `onboard` when you want to initialize or update the saved workspace for a specific instance.

## Quick Start

If you want each instance to have its own dedicated workspace from the start, pass both `--config` and `--workspace` during onboarding.

**Initialize instances:**

```bash
# Create separate instance configs and workspaces
atom onboard --config ~/.atom-personal/config.json --workspace ~/.atom-personal/workspace
atom onboard --config ~/.atom-work/config.json --workspace ~/.atom-work/workspace
atom onboard --config ~/.atom-staging/config.json --workspace ~/.atom-staging/workspace
```

**Configure each instance:**

Edit `~/.atom-personal/config.json`, `~/.atom-work/config.json`, etc. with different channel settings — typically a separate Telegram bot token per instance. The workspace you passed during `onboard` is saved into each config as that instance's default workspace.

**Run instances:**

```bash
# Check one instance before starting it
atom status --config ~/.atom-personal/config.json

# Instance A - personal bot
atom gateway --config ~/.atom-personal/config.json

# Instance B - work bot
atom gateway --config ~/.atom-work/config.json --port 18791

# Instance C - staging bot with its own port
atom gateway --config ~/.atom-staging/config.json --port 18792
```

## Path Resolution

When using `--config`, atom derives its runtime data directory from the config file location. The workspace still comes from `agents.defaults.workspace` unless you override it with `--workspace`.

To open a CLI session against one of these instances locally:

```bash
atom agent -c ~/.atom-personal/config.json -m "Hello from the personal instance"
atom agent -c ~/.atom-work/config.json -m "Hello from the work instance"

# Run the gateway for a specific instance
atom gateway -c ~/.atom-personal/config.json

# Optional one-off workspace override
atom agent -c ~/.atom-personal/config.json -w /tmp/atom-personal-test
```

> `atom agent` starts a local CLI agent using the selected workspace/config. It does not attach to or proxy through an already running `atom gateway` process.

| Component | Resolved From | Example |
|-----------|---------------|---------|
| **Config** | `--config` path | `~/.atom-A/config.json` |
| **Workspace** | `--workspace` or config | `~/.atom-A/workspace/` |
| **Cron Jobs** | workspace directory | `~/.atom-A/workspace/cron/` |
| **Media / runtime state** | config directory | `~/.atom-A/media/` |

## How It Works

- `--config` selects which config file to load
- By default, the workspace comes from `agents.defaults.workspace` in that config
- If you pass `--workspace`, it overrides the workspace from the config file

## Minimal Setup

1. Copy your base config into a new instance directory.
2. Set a different `agents.defaults.workspace` for that instance.
3. Start the instance with `--config`.

Example config fragment:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.atom-personal/workspace"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

The copied base config can keep using the same `modelPresets` and `agents.defaults.modelPreset`. If this instance needs a different model, add another preset and set `agents.defaults.modelPreset` to that preset name.

Start separate instances:

```bash
atom status --config ~/.atom-personal/config.json
atom gateway --config ~/.atom-personal/config.json
atom gateway --config ~/.atom-work/config.json --port 18791
```

Each gateway instance also exposes a lightweight HTTP health endpoint on `gateway.host:gateway.port`. By default, the gateway binds to `127.0.0.1`, so the endpoint stays local unless you explicitly set `gateway.host` to a public or LAN-facing address.

- `GET /health` returns `{"status":"ok"}`
- Other paths return `404`

Override workspace for one-off runs when needed:

```bash
atom gateway --config ~/.atom-personal/config.json --workspace /tmp/atom-personal-test
```

## Common Use Cases

- Run separate Telegram bots, each with its own token and personality
- Keep testing and production instances isolated
- Use different models or providers for different teams
- Serve multiple tenants with separate configs and runtime data

## Notes

- Each instance must use a different port if they run at the same time
- Use a different workspace per instance if you want isolated memory, sessions, and skills
- `--workspace` overrides the workspace defined in the config file
- Cron jobs are stored in the active workspace; runtime media/state is derived from the config directory
