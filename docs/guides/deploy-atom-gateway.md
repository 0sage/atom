# How to Deploy a Long-Running atom AI Agent Gateway

The atom gateway is the long-running self-hosted AI agent process that keeps
chat apps, automations, local triggers, heartbeat jobs, and Dream online.

## What you will build

- a verified atom config
- a gateway process
- a service deployment path with systemd or macOS
  LaunchAgent

## When to use this

Use this when atom should keep running after a single CLI turn. Chat apps,
background automations, local triggers, and server-side integrations all depend
on a live gateway.

## Install

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
atom status
atom agent -m "Hello!"
```

## Minimal working example

Run the gateway in the foreground:

```bash
atom gateway
```

To run it without an attached terminal:

```bash
atom gateway --background
atom gateway status
atom gateway logs
```

## Production notes

- systemd user services are useful for Linux user-level gateway deployments.
- macOS LaunchAgent keeps the gateway alive after login.
- Persist config, workspace, sessions, memory files, channel login state, and
  generated artifacts.
- Restart the gateway after editing `config.json`.

## Security notes

- Plan ports before exposing services. Gateway health defaults to `18790` and
  `atom serve` defaults to `8900`.
- Bind externally only when you have configured tokens or API keys.
- Keep chat access control intentional before deploying.
- Use Linux sandboxing when shell tools are enabled for unattended
  work.

## Troubleshooting

- Use the same `--config` and `--workspace` flags for status checks and service
  startup.
- Check logs with `journalctl`, LaunchAgent logs, or
  `atom gateway --verbose`.
- If another host cannot reach the gateway health port, confirm the service is
  not bound only to loopback.

## Related atom docs

- [Deployment](../deployment.md)
- [Multiple Instances](../multiple-instances.md)
- [Configuration](../configuration.md)
