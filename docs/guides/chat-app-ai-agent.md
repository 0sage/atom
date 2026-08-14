# How to Connect an AI Agent to Chat Apps with atom

atom can run as a self-hosted chatbot or AI agent in Telegram. The gateway
receives chat messages, runs the agent, and sends replies back to the same
channel.

## What you will build

- a working local agent
- one enabled chat channel
- a running gateway
- a pairing-based approval flow or a narrow static allowlist

## When to use this

Use chat apps when the agent should live where users already communicate:
private DMs, team channels, group chats, email threads, or bot workspaces.

## Install

```bash
uv tool install "git+https://github.com/0sage/atom.git"
atom onboard --wizard
atom agent -m "Hello!"
```

Get that reply working before adding a channel. Then follow the platform guide for the bot/account prerequisites:

- [Telegram AI agent](./telegram-ai-agent.md)

## Minimal working example

Channel setup follows this shape:

1. Get the bot token from `@BotFather`.
2. Run `atom plugins enable telegram` to install its optional support.
3. Merge the channel's config snippet into `~/.atom/config.json`.
4. Start `atom gateway` and send a private test message.
5. Approve the pairing code with `/pairing approve <code>` when the first DM asks for one.

See the full [Chat Apps reference](../chat-apps.md#setup) for the config snippet.

Check status from the terminal when you need a lower-level confirmation:

```bash
atom channels status
```

Keep the gateway running so the channel stays connected:

```bash
atom gateway
```

Use the full [Chat Apps reference](../chat-apps.md) when you manage `config.json` directly or need platform-specific advanced settings.

## Production notes

- Keep the gateway running as a service for always-on chat apps.
- Use mention-only group policies before opening a bot to busy channels.
- Use one channel at a time while debugging.
- Prefer DMs for first tests; pairing only works in DMs, and group chats add
  permissions and routing behavior.

## Security notes

- Prefer pairing or explicit allowlists; do not use `allowFrom: ["*"]` outside
  an intentional sandbox.
- Rotate bot tokens if they are pasted into logs or shared files.
- Review file, shell, and web tool access before inviting other users.

## Troubleshooting

- If `atom channels status` does not show the channel, the config key or
  optional dependency is likely missing.
- If the first DM returns a pairing code, approve it with `/pairing approve <code>` from an authorized chat.
- If messages do not arrive, run `atom gateway --verbose` and compare
  platform credentials, event permissions, and allow lists.
- If group replies are unexpected, review that channel's group policy.

## Related atom docs

- [Chat Apps](../chat-apps.md)
- [Configuration](../configuration.md#channel-settings)
- [Pairing](../configuration.md#pairing)
- [Deployment](../deployment.md)
