# Connect Telegram to atom

This guide connects one Telegram bot to atom. Messages sent to that bot use
your normal atom model, tools, memory, and workspace.

## What this guide builds

- a Telegram bot created through BotFather
- the `telegram` channel enabled in atom
- a running atom gateway
- one pairing-approved Telegram account

## Prerequisites

- A working atom CLI reply:

```bash
atom agent -m "Hello!"
```

- A Telegram account.
- A bot token from `@BotFather`.

## Install atom

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
```

## Connect Telegram

Install Telegram support:

```bash
atom plugins enable telegram
```

Then merge this snippet into `~/.atom/config.json`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "proxy": "http://127.0.0.1:7890"
    }
  }
}
```

Omit `proxy` when the gateway can reach Telegram directly.

Omitting `allowFrom` enables pairing-only mode. The first DM from a new user
gets a pairing code instead of agent access.

Telegram uses long polling by default. Webhook mode is available for public
HTTPS deployments; start with long polling for the first test.

## Run atom gateway

```bash
atom channels status
atom gateway
```

Leave the gateway running while you test messages.

## Test a message

Open Telegram, DM the bot, and send:

```text
Hello from Telegram
```

The bot should reply with a pairing code. Approve it from an already trusted
surface, such as the local CLI:

```bash
atom agent -m "/pairing approve ABCD-EFGH"
```

Send the message again after approval. The reply should use the same model and
workspace as your local CLI check.

## Security notes

- Prefer pairing-only mode for first setup. Add `allowFrom` only when you want a
  static allowlist instead of code approval.
- Do not use `allowFrom: ["*"]` unless the bot is isolated or intentionally public.
- Rotate the BotFather token if it is pasted into logs or shared files.
- Review tool access before adding group chats or more users.

## Troubleshooting

- If the channel is not listed, run `atom plugins enable telegram` again in
  the same Python environment.
- If the token is saved but atom cannot reach Telegram, confirm the gateway can
  reach `api.telegram.org`, or configure a proxy for the channel.
- If Telegram rejects the token, copy the current token from BotFather or
  regenerate it.
- If messages do not arrive, run `atom gateway --verbose` and confirm the
  Telegram channel is enabled.
- If a first DM returns a pairing code, that is expected. Approve the code before
  testing normal agent replies.
- If Telegram Web shows unsupported rich messages, keep `richMessages` disabled.

## Next: memory, automations, MCP tools

- [Chat Apps reference](../chat-apps.md)
- [AI Agent Memory](./ai-agent-memory.md)
- [Long-running AI Agent](./long-running-ai-agent.md)
- [Configure MCP tools](./configure-mcp-tools.md)
