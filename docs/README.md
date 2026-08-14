# atom Documentation

Use these docs to get a working agent first, then open a task guide only when you need the next capability. Source-level design and extension details are kept in the contributor section.

These docs are the only docs. They live in the repository and always describe the source tree you installed from, since atom installs straight from git rather than from a published release.

## Start Here

| Your situation | Read this | You are done when... |
|---|---|---|
| Terminals, Python, or API keys are new to you | [Beginner walkthrough](./start-without-technical-background.md) | You can send `Hello!` and receive a reply |
| You are comfortable running commands | [Install and Quick Start](./quick-start.md) | `atom status` is healthy and the CLI can get one reply |
| Something already failed | [Troubleshooting](./troubleshooting.md) | You have isolated the problem to install, config, model, gateway, channel, or tool access |

The recommended first-run path is:

1. Install atom.
2. Configure a provider and model with `atom onboard --wizard`.
3. Send `Hello!` with `atom agent -m "Hello!"` before configuring anything else.
4. Start `atom gateway` once you want chat channels and automations running.

Most people do not need to hand-edit JSON for the first run; the wizard writes the initial provider and model. After that, add chat apps from [Chat Apps](./chat-apps.md) and tool servers from [Configure MCP Tools](./guides/configure-mcp-tools.md).

## Add One Capability

Pick the row that matches what you want to accomplish next:

| Goal | Guide |
|---|---|
| Connect Telegram | [Chat Apps](./chat-apps.md) |
| Choose a hosted, OAuth, company, or local model | [Provider Cookbook](./provider-cookbook.md) |
| Add model fallbacks | [Configure Model Fallback](./guides/configure-model-fallback.md) |
| Enable web search | [Configure Web Search](./guides/configure-web-search.md) |
| Add an MCP tool server | [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Generate images | [Image Generation](./image-generation.md) |
| Schedule work or create a local trigger | [Automations](./automations.md) |
| Understand and manage long-term memory | [Memory](./memory.md) |
| Run atom continuously | [Deployment](./deployment.md) |
| Run separate bots or workspaces | [Multiple Instances](./multiple-instances.md) |
| Call atom from Python | [Python SDK](./python-sdk.md) |
| Expose an OpenAI-compatible endpoint | [OpenAI-Compatible API](./openai-api.md) |

For shorter, outcome-focused walkthroughs, browse the [task guide index](./guides/README.md).

## Operate atom

| Need | Read |
|---|---|
| Commands and flags | [CLI Reference](./cli-reference.md) |
| In-chat slash commands | [In-Chat Commands](./chat-commands.md) |
| Config, workspace, gateway, sessions, tools, and memory in plain language | [Concepts](./concepts.md) |
| Provider/model matching and selection | [Providers and Models](./providers.md) |
| Setup and runtime diagnosis | [Troubleshooting](./troubleshooting.md) |
| Older development highlights | [Release Archive](./release-archive.md) |

## Reference

Use reference pages to look up an exact option after you know what you are trying to configure:

| Area | Reference |
|---|---|
| Every configuration field and default | [Configuration](./configuration.md) |
| Provider and model behavior | [Providers and Models](./providers.md) |
| Chat channel prerequisites and manual JSON | [Chat Apps](./chat-apps.md) |
| Python SDK classes, events, sessions, and hooks | [Python SDK](./python-sdk.md) |
| OpenAI-compatible HTTP routes and payloads | [OpenAI-Compatible API](./openai-api.md) |
| Runtime self-inspection and tuning | [My Tool](./my-tool.md) |

Configuration examples are usually snippets to merge into `~/.atom/config.json`, not complete replacement files. The docs use camelCase because atom writes config that way. Keep real API keys, bot tokens, and passwords out of issues and public logs.

## Extend

These pages explain implementation and extension points. You do not need them to install or operate atom.

| Goal | Read |
|---|---|
| Understand source ownership and runtime flow | [Architecture](./architecture.md) |
| Set up a development environment | [Development](./development.md) |
| Add a channel package | [Channel Package Guide](./channel-package-guide.md) |

If a command or option no longer matches these docs, correct the page in this repository — `atom --version` and the failing command are the useful details to record.
