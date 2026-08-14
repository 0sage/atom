# Chat Apps for Self-Hosted AI Agents

Connect atom to Telegram. Telegram is the only chat channel atom ships
with; this page is its full reference. For a narrative, step-by-step path, use
the [Telegram AI Agent guide](./guides/telegram-ai-agent.md).

Want to build your own channel? See the [Channel Package Guide](./channel-package-guide.md).

Before configuring a chat app, make sure the local CLI path works:

```bash
atom agent -m "Hello!"
```

If that fails, fix installation, config, provider, or model setup first with [`quick-start.md`](./quick-start.md), [`providers.md`](./providers.md), and [`troubleshooting.md`](./troubleshooting.md). Chat apps require `atom gateway` to stay running after the channel is configured.

## Setup

| Channel | What you need |
|---------|---------------|
| **Telegram** | Bot token from [@BotFather](https://t.me/BotFather) |

**1. Install the channel's support package**

```bash
atom plugins enable telegram
```

This installs the channel's manifest-declared dependencies and marks it enabled.
Run it in the same Python environment as atom. To turn the channel off later,
run `atom plugins disable telegram` — atom keeps the saved settings but
stops loading the channel after the next restart.

**2. Create a bot**

- Open Telegram and search for `@BotFather`
- Send `/newbot` and follow the prompts
- Copy the token it gives you

**3. Configure**

The snippet below is meant to be merged into `~/.atom/config.json`, not to
replace the file:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> You can find your **User ID** in Telegram settings. It is shown as `@yourUserId`. Copy this value **without the `@` symbol** and paste it into the config file.

`allowFrom` is a static allowlist. For pairing-based access, omit it entirely:
the first DM receives a pairing code, which you then approve with
`/pairing approve <code>`. See [Pairing](./configuration.md#pairing).

> `allowFrom: ["*"]` bypasses pairing and allows anyone who can reach the bot to talk to it. Use it only when that is intentional, or temporarily while testing in a private sandbox.

**4. Confirm atom sees the channel**

```bash
atom channels status
```

If the channel does not show as enabled, the config snippet is in the wrong
place, the channel name is misspelled, or the config file you edited is not the
one atom is reading.

**5. Start the gateway and leave that terminal running**

```bash
atom gateway
```

**6. Send a test DM**

If the bot returns a pairing code, approve it and send the message again. In
group chats, `groupPolicy` defaults to `mention`, so the bot only answers when
mentioned; set it to `open` to answer every group message.

If the channel is enabled but messages do not arrive, run
`atom gateway --verbose` and compare the token and allow list against what
Telegram shows. [`troubleshooting.md`](./troubleshooting.md) has a symptom table.

## Options

Common fields, all under `channels.telegram`:

| Field | Default | Purpose |
|---|---|---|
| `token` | `""` | Bot token from BotFather. Required. |
| `allowFrom` | `[]` | Static allowlist of user IDs. Omit to use pairing. |
| `groupPolicy` | `"mention"` | `mention` answers only when mentioned in groups; `open` answers everything. |
| `proxy` | unset | HTTP, HTTPS, SOCKS5, or SOCKS5H proxy URL. |
| `replyToMessage` | `false` | Send replies threaded to the triggering message. |
| `reactEmoji` | `"👀"` | Emoji reaction acknowledging a received message. |
| `richMessages` | `false` | Opt in to Bot API 10.1 rich message rendering. |
| `inlineKeyboards` | `false` | Enable inline keyboard buttons. |
| `streaming` | `true` | Stream the reply by editing the message in place. |
| `mode` | `"polling"` | `polling` or `webhook`. |

See [`configuration.md`](./configuration.md) for the shared channel fields such
as `sendProgress` and `sendToolHints`.

### Proxy

If the gateway cannot reach Telegram directly, add a proxy to the same section:

```json
{
  "channels": {
    "telegram": {
      "proxy": "http://127.0.0.1:7890"
    }
  }
}
```

HTTP, HTTPS, SOCKS5, and SOCKS5H proxy URLs are accepted. Treat a proxy URL
containing a username or password as a secret.

### Rich messages

`richMessages` defaults to `false`. Set it to `true` only if your Telegram client
supports Bot API 10.1 rich messages and you want richer markdown rendering; keep
it disabled for Telegram Web, which may show unsupported-message errors for rich
messages.

### Webhook mode

Telegram uses long polling by default. To receive updates through a webhook, expose a public HTTPS URL that forwards to atom's local listener and set `mode` to `webhook`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "mode": "webhook",
      "webhookUrl": "https://example.com/telegram",
      "webhookListenHost": "127.0.0.1",
      "webhookListenPort": 8081,
      "webhookPath": "/telegram",
      "webhookSecretToken": "CHANGE_ME_RANDOM_SECRET",
      "webhookMaxConnections": 4,
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> `webhookSecretToken` is required in webhook mode. Do not expose the local webhook listener directly to the public internet without a reverse proxy or tunnel in front of it. TLS/Host policy is handled by your proxy; atom only listens on `webhookListenHost:webhookListenPort` and validates Telegram's webhook secret token. `webhookMaxConnections` defaults to `4`; atom still serializes Telegram updates per conversation before forwarding them to the agent.
>
> `webhookUrl` is the public HTTPS URL registered with Telegram. `webhookPath` is the local path atom listens on. They often use the same path, but may differ when a reverse proxy or tunnel rewrites the request path.
