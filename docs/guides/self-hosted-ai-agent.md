# How to Run a Self-Hosted AI Agent with atom

This guide sets up atom as a self-hosted AI agent runtime on your own
machine or server. The result is a gateway process that can serve chat apps,
automations, and API integrations.

## What you will build

- a atom config and workspace under your control
- a model provider connected through `config.json`
- a long-running `atom gateway`
- optional chat app and API access

## When to use this

Use this path when you want local or server-side ownership of the agent process,
workspace files, memory files, and provider keys. It is also the right path when
the agent must keep running after one terminal command finishes.

## Install

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
atom agent -m "Hello!"
```

Complete the CLI check before deploying the gateway. A deployment problem is
much easier to debug after the provider and model are known to work.

## Minimal working example

For chat apps and automations, start the gateway:

```bash
atom gateway
```

Connect a channel in `~/.atom/config.json`, then keep the same gateway
process running for messages.

## Production notes

- Use systemd or a macOS LaunchAgent when the process should survive
  terminal exits.
- Give every deployed instance a distinct config path, workspace path, and port
  set.
- Keep secrets in environment variables and start the service from the same
  environment.
- Use health checks against the gateway or API process, not chat app delivery as
  the only signal.

## Security notes

- Bind local-only services to `127.0.0.1` unless you intentionally expose them.
- Set an API key before binding the OpenAI-compatible API to a public interface.
- Prefer pairing for DM-capable chat apps, and keep any static `allowFrom`
  allowlists strict.
- Enable `tools.restrictToWorkspace`; on Linux, use the bubblewrap sandbox for
  shell execution.

## Troubleshooting

- Run `atom status` with the same `--config` and `--workspace` flags used by
  the service.
- Run `atom gateway --verbose` while debugging channel startup.
- Check port conflicts if the gateway health endpoint or `atom serve` fails
  to bind.

## Related atom docs

- [Deployment](../deployment.md)
- [Multiple Instances](../multiple-instances.md)
- [Configuration](../configuration.md)
- [Chat Apps](../chat-apps.md)
- [OpenAI-Compatible API](../openai-api.md)
