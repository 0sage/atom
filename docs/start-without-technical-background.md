# Start Without Technical Background

This walkthrough is for people who have not used a terminal, API key, or JSON config file before. The goal is only to get one reply from atom. You do not need to understand atom's architecture or edit its config by hand.

## What You Will Need

- A macOS or Linux computer.
- An account or endpoint that can run an AI model.
- The API key, endpoint, and model name required by that service. A local model server may not require an API key.

An API key is password-like. Do not post it in an issue, screenshot, chat, or public config file.

## A Few Useful Words

| Word | Meaning |
|---|---|
| Terminal | A text window where you paste a command and press Enter |
| Command | One instruction typed into the terminal |
| Provider | The service or local server that runs the AI model |
| Model ID | The exact model name expected by that provider |
| API key | A secret credential that lets software call the provider |
| Wizard | A question-and-answer setup menu |
| Gateway | The long-running atom process that keeps chat apps connected |

## 1. Open a Terminal

| System | How |
|---|---|
| macOS | Press `Command+Space`, type `Terminal`, and press Enter |
| Linux | Open your application menu and search for Terminal |

You do not need to install Python yourself. The installer in step 3 brings its own.

## 2. Prepare Your Model Details

atom does not create an AI provider account for you. Before setup, have these details nearby:

1. The provider or company endpoint name.
2. Its API key, if it requires one.
3. Its base URL, if its documentation gives you one.
4. A model ID your account can use.

The provider, credential, endpoint, and model must belong together. For example, an API key from one provider usually cannot call a model name copied from a different provider.

## 3. Install atom

atom is installed with a tool called `uv`. Install `uv` first — copy the line
below, paste it into the terminal, and press Enter. Copy only the text inside the
code block.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the terminal and open a new one, so it picks up the new command. Then
install atom:

```bash
uv tool install "git+https://github.com/0sage/atom.git"
```

This downloads atom into its own isolated Python environment, so it cannot
disturb anything else on your computer. The first run can take a few minutes.
Keep the terminal open until it finishes.

If a later command reports that `atom` was not found, run `uv tool update-shell`
once, then close and reopen the terminal.

## 4. Configure Your Model

Run the setup wizard:

```bash
atom onboard --wizard
```

It asks you, one question at a time, to:

1. Choose your provider.
2. Enter its API key and base URL when required.
3. Name a model preset.
4. Enter a model ID available to your provider account.

Treat every API key like a password. Do not include it in screenshots or support requests.

If the terminal cannot find `atom`, run `uv tool update-shell`, then close and reopen the terminal. You can also run it without installing: `uv tool run --from "git+https://github.com/0sage/atom.git" atom onboard --wizard`.

The wizard saves two things on your computer:

| Path | What it holds |
|---|---|
| `~/.atom/config.json` | Your provider, model, and other settings |
| `~/.atom/workspace/` | Conversation history, memory, and files atom creates |

To confirm the result without spending any model credits, run:

```bash
atom status
```

## 5. Get the First Reply

Send one message:

```bash
atom agent -m "Hello!"
```

A normal assistant reply means setup is complete. The exact reply does not matter.

For a back-and-forth conversation in the terminal, run:

```bash
atom agent
```

Press Enter to send a message. Type `exit` or press `Ctrl+D` when you are finished.

## 6. Add One Thing at a Time

Do not configure every feature immediately. Choose one next goal:

| Goal | What to do |
|---|---|
| Change the AI model | Run `atom onboard --wizard` again, or read [Providers and Models](./providers.md) |
| Talk to atom from Telegram | Follow the [Chat Apps guide](./chat-apps.md), then start `atom gateway` |
| Add a tool integration | Read [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Schedule a reminder or recurring task | Ask atom in a chat, then read [Automations](./automations.md) |
| Work with project files | Read [Concepts](./concepts.md) to understand workspaces and access modes first |

Most people go to a chat app next: it lets you message atom from your phone instead of the terminal. That path needs the gateway running:

```bash
atom gateway
```

Leave that terminal open. The gateway is what keeps your chat apps connected, so replies stop arriving when you close it.

For a chat platform's account, bot, token, or permission prerequisites, use the [Chat Apps guide](./chat-apps.md). For local models and provider-specific recipes, use the [Provider Cookbook](./provider-cookbook.md).

Some changes to `~/.atom/config.json` only take effect after a restart. Return to the terminal, press `Ctrl+C`, and run the command again.

## If Something Fails

Run these commands one at a time:

```bash
atom --version
atom status
atom agent -m "Hello!"
```

| What you see | What it usually means |
|---|---|
| `atom: command not found` | Run `uv tool update-shell`, then close and reopen the terminal |
| `401`, unauthorized, or invalid API key | The key is wrong, expired, or belongs to a different provider |
| Model not found | The model ID is misspelled or unavailable to your provider account |
| A chat app never replies | Check that `atom gateway` is still running, then run `atom channels status` |
| A change was saved but nothing changed | Restart atom so the running process reloads the config |

If you ask for help, include your operating system, `atom --version`, `atom status`, the exact command, and the exact error. Remove every API key, bot token, password, OAuth token, and private account ID first.

Continue with the full [Troubleshooting guide](./troubleshooting.md) for an ordered diagnosis.

## Use atom Later

For a quick question in the terminal:

```bash
atom agent
```

To keep your chat apps and scheduled tasks running:

```bash
atom gateway
```

Leave that terminal open while atom runs. To stop it, return to the terminal and press `Ctrl+C`. Once the foreground start works, `atom gateway --background` runs it without an attached terminal; manage it with `atom gateway status`, `logs`, `restart`, and `stop`.
