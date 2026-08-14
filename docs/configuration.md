# Configuration

Config file: `~/.atom/config.json`

This is the full reference. If this is your first install, start with [`quick-start.md`](./quick-start.md). If you are trying to choose a model or fix provider/model matching, use [`providers.md`](./providers.md) first and come back here for exact fields and advanced options.

For a first local setup, prefer `atom onboard --wizard` before editing JSON: it writes the initial provider, model preset, and workspace paths for you. Edit `config.json` directly when you need an advanced field, automate deployment, or intentionally manage configuration as code.

The JSON examples below are usually partial snippets to merge into your existing config, not full replacement files. For the mental model behind config, workspace, gateway, channels, sessions, tools, and memory, see [`concepts.md`](./concepts.md).

The generated `config.json` uses camelCase keys such as `apiKey` and `intervalS`. snake_case keys are also accepted for compatibility, but the docs prefer camelCase because that is what atom writes back to disk.

For setup and runtime failures, follow the diagnosis order in [`troubleshooting.md`](./troubleshooting.md) before changing multiple config areas at once.

> [!NOTE]
> If your config file is older than the current schema, run `atom onboard --refresh`. atom adds missing default fields while preserving your existing values.

## Configuration Guides

This page is the complete configuration reference. For task-oriented setup, use
the focused guides first and come back here for exact fields and defaults.

| Task | Guide |
|---|---|
| Add MCP tools | [`guides/configure-mcp-tools.md`](./guides/configure-mcp-tools.md) |
| Enable web search and web fetch | [`guides/configure-web-search.md`](./guides/configure-web-search.md) |
| Configure model fallback | [`guides/configure-model-fallback.md`](./guides/configure-model-fallback.md) |
| Add an OpenAI-compatible provider | [`guides/configure-openai-compatible-provider.md`](./guides/configure-openai-compatible-provider.md) |
| Add Langfuse observability | [`guides/configure-langfuse-observability.md`](./guides/configure-langfuse-observability.md) |
| Secure a local AI agent | [`guides/secure-local-ai-agent.md`](./guides/secure-local-ai-agent.md) |
| Deploy the gateway | [`guides/deploy-atom-gateway.md`](./guides/deploy-atom-gateway.md) |

## Quick Jump

| Need | Section |
|---|---|
| Keep secrets out of `config.json` | [Environment Variables for Secrets](#environment-variables-for-secrets) |
| Tune process-level behavior with env vars | [Runtime Environment Variables](#runtime-environment-variables) |
| Trace model calls | [Langfuse Observability](#langfuse-observability) |
| Configure credentials and endpoints | [Providers](#providers) |
| Name and switch model choices | [Model Presets](#model-presets) |
| Add fallback chains | [Model Fallbacks](#model-fallbacks) |
| Configure voice transcription | [Transcription Settings](#transcription-settings) |
| Tune channel defaults | [Channel Settings](#channel-settings) |
| Configure web search and fetch | [Web Tools](#web-tools) |
| Enable image generation | [Image Generation](#image-generation) |
| Add MCP servers | [MCP](#mcp-model-context-protocol) |
| Review shell, workspace, and SSRF controls | [Security](#security) |
| Control access and pairing | [Pairing](#pairing) |
| Tune gateway jobs, sessions, and tools | [Gateway Heartbeat](#gateway-heartbeat), [Auto Compact](#auto-compact), [Unified Session](#unified-session), [Tool Hint Max Length](#tool-hint-max-length) |

## Where a Setting Lives

Start from the task below. Most changes touch one config section and one verification command.

| Task | First keys to check | Verify with | Deep dive |
|---|---|---|---|
| Make the first model reply work | `providers.<name>.apiKey`, optional `providers.<name>.apiBase`, `modelPresets.<preset>`, `agents.defaults.modelPreset` | `atom status`, then `atom agent -m "Hello!"` | [Providers](#providers), [Model Presets](#model-presets) |
| Add fallback models | `modelPresets.<fallback>`, `agents.defaults.fallbackModels` | `atom status`, then a normal agent run | [Model Fallbacks](#model-fallbacks) |
| Keep secrets out of the config file | `${ENV_VAR}` placeholders inside any string value | Start atom from the same environment that sets the variable | [Environment Variables for Secrets](#environment-variables-for-secrets) |
| Expose an OpenAI-compatible HTTP API | `api.host`, `api.port`, `api.apiKey` | `atom serve`, then a `/v1/chat/completions` request | [OpenAI-Compatible API](./openai-api.md) |
| Connect one chat app | `channels.<channel>.enabled`, channel credentials, optional pairing or `channels.<channel>.allowFrom` | `atom channels status`, then `atom gateway --verbose` | [Channel Settings](#channel-settings), [Chat Apps](./chat-apps.md) |
| Enable voice transcription | `transcription.enabled`, `transcription.provider`, matching `providers.<name>.apiKey` | Send or upload a short voice message through a configured surface | [Transcription Settings](#transcription-settings) |
| Enable web search or fetch | `tools.web.search.*`, `tools.web.fetch.*`, optional `tools.ssrfWhitelist` | Ask a question that requires current web information, then inspect logs if needed | [Web Tools](#web-tools), [Security](#security) |
| Enable image generation | `tools.imageGeneration.enabled`, `tools.imageGeneration.provider`, `tools.imageGeneration.model`, matching provider credentials | Send one image request through a configured surface | [Image Generation](#image-generation) |
| Add external tools through MCP | `tools.mcpServers.<name>` | Start `atom gateway --verbose` and check startup/tool logs | [MCP](#mcp-model-context-protocol) |
| Tighten tool and network safety | `tools.restrictToWorkspace`, `tools.exec.sandbox`, `tools.ssrfWhitelist`, `channels.*.allowFrom` | Run the same workflow through the channel or CLI you plan to expose | [Security](#security), [Pairing](#pairing) |
| Tune request timeouts or process concurrency | `ATOM_LLM_TIMEOUT_S`, `ATOM_STREAM_IDLE_TIMEOUT_S`, `ATOM_MAX_CONCURRENT_REQUESTS` | Start atom from the same environment and inspect startup/runtime logs | [Runtime Environment Variables](#runtime-environment-variables) |
| Run multiple isolated bots | separate `--config` and `--workspace` paths, plus distinct `gateway.port` or channel ports when processes run together | Use the same explicit paths with `atom status`, `agent`, `gateway`, and `serve` | [Multiple Instances](./multiple-instances.md), [CLI Reference](./cli-reference.md) |
| Observe model calls | `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL` environment variables | Run one model call, then check the matching Langfuse project | [Langfuse Observability](#langfuse-observability) |

## Environment Variables for Secrets

Instead of storing secrets directly in `config.json`, you can use `${VAR_NAME}` references that are resolved from environment variables at startup:

```json
{
  "channels": {
    "telegram": { "token": "${TELEGRAM_TOKEN}" },
    "email": {
      "imapPassword": "${IMAP_PASSWORD}",
      "smtpPassword": "${SMTP_PASSWORD}"
    }
  },
  "providers": {
    "groq": { "apiKey": "${GROQ_API_KEY}" }
  }
}
```

Any string value in `config.json` can use `${VAR_NAME}`. Resolution runs once at startup, in memory only — resolved values are never written back to disk, so editing config through `atom onboard` preserves the placeholder.

If a referenced variable is unset, atom fails fast and reports the exact config field
and variable name without echoing the field value. Run `atom status` with the same
`--config` path to inspect the problem.

### More examples

**MCP servers** — both stdio `env` and HTTP `headers`:

```json
{
  "tools": {
    "mcpServers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
      },
      "remote": {
        "url": "https://example.com/mcp/",
        "headers": { "Authorization": "Bearer ${REMOTE_MCP_TOKEN}" }
      }
    }
  }
}
```

**Web search providers:**

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "${BRAVE_API_KEY}"
      }
    }
  }
}
```

### Loading variables at startup

Pick whatever fits your deployment — atom only reads `os.environ` at startup, so any mechanism that populates the process environment works.

**systemd** — use `EnvironmentFile=` in the service unit to load variables from a file that only the deploying user can read:

```ini
# /etc/systemd/system/atom.service (excerpt)
[Service]
EnvironmentFile=/home/youruser/atom_secrets.env
User=atom
ExecStart=...
```

```bash
# /home/youruser/atom_secrets.env (mode 600, owned by youruser)
TELEGRAM_TOKEN=your-token-here
IMAP_PASSWORD=your-password-here
```

**direnv** — drop a `.envrc` in your working directory and run `direnv allow`:

```bash
# .envrc (auto-loaded by direnv)
export TELEGRAM_TOKEN=your-token-here
export ANTHROPIC_API_KEY=...
```

**Secret managers (1Password, Bitwarden, pass)** — wrap the process so secrets only exist as env vars for the lifetime of the run, never on disk:

```bash
# 1Password — references in .env.tpl look like `op://Vault/Item/field`
op run --env-file=.env.tpl -- atom agent

# pass (passwordstore.org)
ANTHROPIC_API_KEY="$(pass show api/anthropic)" atom agent

# Bitwarden
ANTHROPIC_API_KEY="$(bw get password api/anthropic)" atom agent
```

## Runtime Environment Variables

These variables are process-level switches. Set them in the same terminal, service unit, container, or supervisor that starts atom.

### Runtime controls

| Variable | Default | Description |
|----------|---------|-------------|
| `ATOM_MAX_CONCURRENT_REQUESTS` | `3` | Maximum concurrently running inbound agent requests. Must be an integer; set `0` or a negative value for unlimited. |
| `ATOM_LLM_TIMEOUT_S` | `300` | Wall-clock timeout, in seconds. Ordinary requests use this value; streaming requests use the greater of 300 seconds or twice this value. Set `0` to disable. |
| `ATOM_STREAM_IDLE_TIMEOUT_S` | `90` | Streaming idle timeout, in seconds, used by streaming providers. Invalid or non-positive values are ignored; values above `3600` are clamped. |
| `ATOM_OPENAI_COMPAT_TIMEOUT_S` | `120` | HTTP request timeout, in seconds, for OpenAI-compatible providers. Invalid or non-positive values are ignored. |
| `ATOM_WORKSPACE_SANDBOX_ENFORCED` | unset | Marks that an external workspace sandbox is already enforced. Truthy values (`1`, `true`, `yes`, `on`, `enabled`) use `ATOM_WORKSPACE_SANDBOX_PROVIDER` as the label; any other non-false value is treated as the provider name. |
| `ATOM_WORKSPACE_SANDBOX_PROVIDER` | `unknown` | Display label for the external workspace sandbox when `ATOM_WORKSPACE_SANDBOX_ENFORCED` is truthy, for example `macos_app_sandbox` or `bwrap`. |
| `ATOM_SANDBOX_ENFORCED` | unset | Legacy compatibility alias for `ATOM_WORKSPACE_SANDBOX_ENFORCED`. |
| `ATOM_TMUX_SOCKET_DIR` | `${TMPDIR:-/tmp}/atom-tmux-sockets` | Socket directory used by the bundled `tmux` skill scripts. |

Internal variables such as `ATOM_RESTART_*` and `ATOM_PATH_*` are set by atom itself and are not a supported user configuration surface.

## Langfuse Observability

atom can trace OpenAI-compatible provider calls through Langfuse's OpenAI SDK wrapper. This is configured with environment variables, not `config.json`.

Install the optional package in the same Python environment that runs atom:

```bash
atom plugins enable langfuse
```

Set Langfuse credentials before starting `atom agent`, `atom gateway`, or `atom serve`:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

When `LANGFUSE_SECRET_KEY` is set and the `langfuse` package is installed, atom uses `langfuse.openai.AsyncOpenAI` for OpenAI-compatible providers so model requests are sent to Langfuse in the background. If the secret key is set but `langfuse` is missing, atom logs a warning and falls back to the regular OpenAI client.

Use the Langfuse region or self-hosted URL that matches your project. The [Langfuse OpenAI SDK docs](https://langfuse.com/integrations/model-providers/openai-py) use `LANGFUSE_BASE_URL` for cloud regions and self-hosted instances.

Tracing covers the providers that go through atom's OpenAI-compatible client path. Native providers that do not use that client may not produce Langfuse OpenAI-wrapper traces.

## Providers

> [!TIP]
> - **Voice transcription**: Voice messages and client audio uploads use the shared top-level `transcription` settings. The default `transcription.provider` value is `"groq"`; set it to `"openai"` for OpenAI Whisper. API keys still live in the matching `providers.<provider>` config.
> - **Base URL overrides**: `providers.anthropic.apiBase` and `providers.openai.apiBase` point a built-in provider at a proxy, gateway, or regional endpoint while keeping its native request format. For Anthropic a trailing `/v1` is normalized away.
> - **Custom OpenAI-compatible providers**: Besides the built-in `custom` provider, any extra key under `providers` can define its own OpenAI-compatible endpoint. For example, `providers.companyProxy.apiBase` plus `modelPresets.primary.provider: "companyProxy"` creates a separate custom provider. Set `apiBase`; set `apiKey` only when the endpoint requires it. The name must not collide with a built-in provider name in any capitalization. This named-custom path uses the OpenAI-compatible request format only. For Anthropic-compatible proxies, use `providers.anthropic.apiBase` with `provider: "anthropic"`.
> - **Local model servers**: Ollama, LM Studio, vLLM, and similar local servers are configured through `custom` (or a named custom key) with `apiBase` pointing at the local port. See [Local model servers](#local-providers).
> - **Provider-scoped proxy**: `providers.<name>.proxy` routes only that provider through an HTTP proxy. It is supported for the OpenAI-compatible providers (`openai`, `groq`, `custom`, named custom keys). The native `anthropic` backend rejects `proxy`; use `providers.anthropic.apiBase` instead.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `anthropic` | LLM (Claude direct, native Messages API, prompt caching) | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM (Chat Completions / Responses) + Voice transcription (Whisper) | [platform.openai.com](https://platform.openai.com) |
| `groq` | LLM + Voice transcription (Whisper, default) | [console.groq.com](https://console.groq.com) |
| `custom` | Any OpenAI-compatible endpoint, including local servers | — |

Any provider not listed above can be reached through `custom` or a named custom provider key, as long as it exposes an OpenAI-compatible API. Anthropic-compatible endpoints go through `providers.anthropic.apiBase` instead.

<details>
<summary><b>OpenAI</b></summary>

By default, OpenAI uses `apiType: "auto"`: atom calls Chat Completions normally and routes GPT-5/o-series or explicit `reasoningEffort` requests through the Responses API when useful. You can force a specific API surface:

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiType": "chat_completions"
    }
  }
}
```

Valid `apiType` values are exactly `auto`, `chat_completions`, and `responses`.

`extraBody` follows the selected OpenAI API surface. With Chat Completions, atom passes
ordinary fields through as the SDK `extra_body` value; list-valued `extraBody.tools` is handled
specially and appended after generated function tools. With Responses, configure it in Responses
API body shape; atom merges ordinary top-level fields into the Responses request body, appends
`extraBody.tools` after generated function tools, and merges `extraBody.include` without duplicates:

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiType": "responses",
      "extraBody": {
        "tools": [{ "type": "web_search" }],
        "include": ["web_search_call.action.sources"]
      }
    }
  }
}
```

A hosted search tool replaces atom's same-name local `web_search` function for that
request, while other tools such as `web_fetch` remain available.

</details>


<a id="responses-state-and-compaction"></a>

### Responses conversation state and compaction

Providers that use the Responses API can keep reasoning context across a
conversation, which helps with multi-step tasks. Supported providers can also
compact long conversations automatically.

atom preserves Responses conversation state automatically for OpenAI Responses models.
Native compaction is also automatic when the provider supports it. The
threshold is derived from the active model's context window and reserved output
headroom; no provider configuration is required.


<details>
<summary><b>Custom Provider (Any OpenAI-compatible API)</b></summary>

Connects directly to any OpenAI-compatible endpoint — llama.cpp, Together AI, Fireworks, or any self-hosted server. Model name is passed as-is.

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "modelPresets": {
    "custom": {
      "provider": "custom",
      "model": "your-model-name"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "custom"
    }
  }
}
```

> For local servers that don't require authentication, set `apiKey` to `null`.
>
> `custom` is the right choice for providers that expose an OpenAI-compatible **chat completions** API. It does **not** force third-party endpoints onto the **Responses API**.
>
> If your proxy or gateway is specifically Responses-API-compatible, use `providers.openai` with `apiBase` and `apiType: "responses"`:
>
> ```json
> {
>   "providers": {
>     "openai": {
>       "apiKey": "your-api-key",
>       "apiBase": "https://api.your-provider.com/v1",
>       "apiType": "responses"
>     }
>   },
>   "modelPresets": {
>     "responsesProxy": {
>       "provider": "openai",
>       "model": "your-model-name"
>     }
>   },
>   "agents": {
>     "defaults": {
>       "modelPreset": "responsesProxy"
>     }
>   }
> }
> ```
>
> `apiType` is only accepted on `providers.openai`; custom and named custom providers reject it.
>
> Anthropic-compatible endpoints are separate: use `providers.anthropic.apiBase` and set the preset provider to `anthropic`. Arbitrary custom provider names do not use the Anthropic Messages API format.
>
> In short: **chat-completions-compatible endpoint → `custom` or a named custom provider**; **Responses-compatible endpoint → `openai` with `apiBase` + `apiType: "responses"`**; **Anthropic-compatible endpoint → `anthropic` with `apiBase`**.

Some OpenAI-compatible gateways expose request-body extensions such as vLLM guided decoding or local sampling controls. Put those under `extraBody`; atom merges them into the chat-completions request body after its provider defaults:

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1",
      "extraBody": {
        "repetition_penalty": 1.15,
        "chat_template_kwargs": {
          "enable_thinking": false
        }
      }
    }
  }
}
```

If a custom OpenAI-compatible endpoint exposes a provider-specific thinking toggle, set `thinkingStyle` so atom can translate `reasoningEffort` into the right request body. Supported styles are `thinking_type` (`{"thinking":{"type":"enabled"}}`), `enable_thinking` (`{"enable_thinking": true}`), and `reasoning_split` (`{"reasoning_split": true}`):

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://api.your-provider.com/v1",
      "thinkingStyle": "enable_thinking"
    }
  },
  "modelPresets": {
    "company": {
      "provider": "companyProxy",
      "model": "served-model-name",
      "reasoningEffort": "high"
    }
  }
}
```

Leave `thinkingStyle` unset unless the endpoint explicitly documents one of those wire formats. `extraBody` is still applied last, so advanced users can override the generated value.

</details>

<a id="local-providers"></a>
<a id="ollama-local"></a>
<a id="atomic-chat-local"></a>
<a id="vllm-local-openai-compatible"></a>
<details>
<summary><b>Local model servers (Ollama, LM Studio, vLLM, and similar)</b></summary>

Local OpenAI-compatible servers are configured through the `custom` provider (or your own
named provider key). Point `apiBase` at the local port and omit `apiKey` when the server
does not require one.

**Ollama** — default port `11434`:

```bash
ollama serve
ollama pull llama3.2
```

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "local": {
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

**LM Studio** — default port `1234`, model name as shown in its server tab:

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://localhost:1234/v1"
    }
  }
}
```

**vLLM and other self-hosted servers** — some require a non-empty placeholder key:

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  }
}
```

Set `model` to the name the server actually serves (`--served-model-name` for vLLM).

To run several local servers at once, give each one its own named provider key
(`providers.labA`, `providers.labB`) and reference that key from the preset.

atom recognizes `localhost`, `127.0.0.1`, and private-range base URLs: it tunes HTTP
keepalive for them, and a `502` or connection-refused failure against such a URL adds a
local-endpoint reachability hint to the error. Provider selection does not infer local
servers from the model name, so keep `provider` explicit in the preset rather than relying
on `"auto"`.

</details>

Contributor notes for adding new providers live in [`development.md`](./development.md#adding-an-llm-provider).

## Model Presets

Model presets let you name a complete model configuration and select one per session with `/model <preset>`. They are the recommended way to configure models because the same names can be reused for new-session defaults, chat-command switching, and fallback chains.

Existing configs do not need to change. Direct `agents.defaults.model`, `provider`, `maxTokens`, `contextWindowTokens`, `temperature`, and `reasoningEffort` fields still define the implicit `default` preset. For new configs, prefer top-level `modelPresets` plus `agents.defaults.modelPreset`.

```json
{
  "modelPresets": {
    "fast": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "localSmall"]
    }
  },
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "model": "gpt-4.1-mini",
      "provider": "openai",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.2,
      "reasoningEffort": "low"
    },
    "deep": {
      "label": "Deep",
      "model": "claude-opus-4-5",
      "provider": "anthropic",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "reasoningEffort": "high"
    },
    "localSmall": {
      "label": "Local Small",
      "model": "llama3.2",
      "provider": "custom",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  }
}
```

`modelPresets` is a top-level object. The keys under it (`fast`, `deep`, `coding`, etc.) are user-defined preset names. Each preset supports:

| Field | Description |
|-------|-------------|
| `label` | Optional display name shown in model lists. |
| `model` | Model name to use for this preset. |
| `provider` | Provider name, or `"auto"` to use provider auto-detection. |
| `maxTokens` | Maximum completion/output tokens. |
| `contextWindowTokens` | Context window size used by prompt building and consolidation decisions. |
| `temperature` | Sampling temperature. |
| `reasoningEffort` | Optional reasoning/thinking setting. Provider support varies. |

`default` is reserved and always means the implicit preset built from direct `agents.defaults.*` fields; do not define `modelPresets.default`. Use `/model default` to switch back to those direct fields in an existing config.

Set `agents.defaults.modelPreset` to choose the preset followed by sessions that have no saved model selection. When `modelPreset` is `null` or omitted, such sessions follow the implicit `default` preset from direct `agents.defaults.*` fields. `/model <preset>` saves an override in the current session, so its future turns keep that preset across process restarts while other sessions remain unchanged. The command does not write the selection back to `config.json`.

### Model Fallbacks

`agents.defaults.fallbackModels` defines an ordered failover chain for the active model configuration. The primary model is still selected by `agents.defaults.modelPreset` or, in older configs, by the implicit `default` preset from direct `agents.defaults.*` fields.

Each fallback candidate can be either:

- A preset name from `modelPresets`, such as `"deep"`. This is the recommended form. The preset's full model, provider, generation, and context-window config is used.
- An inline fallback object with at least `provider` and `model`. Optional `maxTokens`, `contextWindowTokens`, and `temperature` fields inherit from the active primary config when omitted. `reasoningEffort` does not inherit; omit it to leave reasoning off for that fallback, or set it explicitly for models that support reasoning.

Preset fallback chain:

```json
{
  "modelPresets": {
    "fast": {
      "model": "gpt-4.1-mini",
      "provider": "openai",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.2
    },
    "deep": {
      "model": "claude-opus-4-5",
      "provider": "anthropic",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "reasoningEffort": "high"
    },
    "localSmall": {
      "model": "llama3.2",
      "provider": "custom",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "localSmall"]
    }
  }
}
```

String entries are preset names, not raw model names. In the example above, `"deep"` means `modelPresets.deep`; atom will not interpret it as a provider model ID. Changing a preset updates both `/model <preset>` switching and any fallback chain that references it.

Inline fallback object:

```json
{
  "modelPresets": {
    "fast": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": [
        {
          "provider": "openai",
          "model": "gpt-5",
          "maxTokens": 4096,
          "contextWindowTokens": 128000
        }
      ]
    }
  }
}
```

Use inline objects only when a fallback is not worth naming as a reusable preset. `fallbackModels` belongs under `agents.defaults`, not inside individual `modelPresets` entries.

Failover normally runs when the primary provider returns a fallbackable model/provider error before any answer text has been streamed. Stream-stall timeouts are the recovery exception: if the provider already emitted partial answer text and then stalls, atom closes the current stream segment and retries/fails over in a new segment. Typical fallback cases include timeouts, connection errors, 5xx server errors, 429 rate limits, overloads, authentication/permission failures such as invalid or expired credentials, and quota/balance exhaustion. It does not run for malformed requests, content filtering/refusals, or context-length/message-format errors.

If fallback candidates use smaller `contextWindowTokens` values, atom builds context using the smallest window in the active chain so every candidate can receive the same prompt.

## Transcription Settings

Audio transcription is a channel capability: chat-channel voice messages are transcribed automatically before they enter the agent. It is exposed through `Channel.transcribe_audio` (`atom/channels/base.py`), so channels that receive voice messages use it and the OpenAI-compatible API does not.

Configure transcription under the top-level `transcription` section:

```json
{
  "transcription": {
    "enabled": true,
    "provider": "groq",
    "model": null,
    "language": null,
    "maxDurationSec": 120,
    "maxUploadMb": 25
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enables audio transcription for chat-channel voice messages and client audio uploads. |
| `provider` | `"groq"` | Transcription backend: `"groq"` or `"openai"`. |
| `model` | provider default | Optional transcription model override. Defaults to `whisper-large-v3` for Groq and `whisper-1` for OpenAI. Both providers expect a speech-to-text model on the transcription endpoint; chat LLMs are rejected. |
| `language` | `null` | Optional ISO-639 language hint, e.g. `"en"`, `"zh"`, `"ko"`, or `"ja"`. |
| `maxDurationSec` | `120` | Maximum recording duration accepted from a client. |
| `maxUploadMb` | `25` | Maximum audio upload size. |

Provider and language resolution is intentionally ordered for backwards compatibility:

1. `transcription.provider` / `transcription.language`
2. Legacy `channels.transcriptionProvider` / `channels.transcriptionLanguage`
3. Built-in defaults (`provider: "groq"`, no language hint)

The legacy `channels.*` transcription fields existed before transcription became a shared capability across chat channels and client audio uploads. They are still read so older `config.json` files keep working, but they are no longer the preferred configuration surface. If both old and new fields are present, the top-level `transcription` values are the source of truth.

Transcription credentials are intentionally not stored in `transcription`. Put the API key and optional endpoint in the matching provider config:

```json
{
  "providers": {
    "groq": {
      "apiKey": "gsk-...",
      "apiBase": "https://api.groq.com/openai/v1"
    }
  },
  "transcription": {
    "provider": "groq",
    "language": "zh"
  }
}
```

Selecting a transcription provider does not configure credentials by itself. For example, the effective provider may default to Groq for compatibility, but transcription is only usable when `providers.groq.apiKey` or the matching environment-backed config is available. The Settings UI writes only the top-level `transcription` fields.

If you are adding a new transcription provider, see [`development.md`](./development.md#adding-a-transcription-provider).

## Channel Settings

Global settings that apply to all channels. Configure under the `channels` section in `~/.atom/config.json`:

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": true,
    "sendMaxRetries": 3,
    "telegram": {
      "enabled": false
    }
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `sendProgress` | `true` | Stream agent's text progress to the channel |
| `sendToolHints` | `true` | Stream tool-call hints (e.g. `read_file("…")`) |
| `showReasoning` | `true` | Allow channels to surface model reasoning/thinking content (`reasoning_content`, Anthropic `thinking_blocks`, inline `<think>` tags). Reasoning flows as a dedicated stream with `_reasoning_delta` / `_reasoning_end` markers — channels override `send_reasoning_delta` / `send_reasoning_end` to render in-place updates. Even with `true`, channels without those overrides stay no-op silently. Currently surfaced on the CLI; Telegram keeps the base no-op until its bubble UI is adapted. Independent of `sendProgress`. |
| `sendMaxRetries` | `3` | Max delivery attempts per outbound message, including the initial send (0-10 configured, minimum 1 actual attempt) |

Non-image attachments are included in the user message as local path references, without
injecting their contents into the model prompt. When file tools are enabled, the agent
can inspect supported text, PDF, DOCX, XLSX, and PPTX files on demand with `read_file`,
or pass the original path to another tool when exact file bytes are required. The deprecated
`channels.extractDocumentText` setting is accepted for compatibility but ignored.
Normal tool workspace and media access rules still apply to attachment paths.

`channels.transcriptionProvider` and `channels.transcriptionLanguage` are deprecated compatibility fields. They remain as a read-only fallback for older configs, but new configuration should use top-level `transcription.provider` and `transcription.language`.

`sendProgress` and `sendToolHints` can also be overridden per channel. The global values stay as defaults for channels that do not set their own value:

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": true,
    "telegram": {
      "enabled": true,
      "sendProgress": false,
      "sendToolHints": false
    }
  }
}
```

Telegram `richMessages` defaults to `false`. Enable it only to opt in to Bot API 10.1 `sendRichMessage` rendering; leave it disabled for Telegram Web clients that show unsupported-message errors for rich messages.

### Retry Behavior

Retry is intentionally simple.

When a channel `send()` raises, atom retries at the channel-manager layer. By default, `channels.sendMaxRetries` is `3`, and that count includes the initial send.

- **Attempt 1**: Send immediately
- **Attempt 2**: Retry after `1s`
- **Attempt 3**: Retry after `2s`
- **Higher retry budgets**: Backoff continues as `1s`, `2s`, `4s`, then stays capped at `4s`
- **Transient failures**: Network hiccups and temporary API limits often recover on the next attempt
- **Permanent failures**: Invalid tokens, revoked access, or banned channels will exhaust the retry budget and fail cleanly

> [!NOTE]
> This design is deliberate: channel implementations should raise on delivery failure, and the channel manager owns the shared retry policy.
>
> Some channels may still apply small API-specific retries internally. For example, Telegram separately retries timeout and flood-control errors before surfacing a final failure to the manager.
>
> If a channel is completely unreachable, atom cannot notify the user through that same channel. Watch logs for `Failed to send to {channel} after N attempts` to spot persistent delivery failures.

## Web Tools

atom incorporates basic tools for accessing the web. These include searching via APIs, and fetching arbitrary web pages in Markdown format. They are enabled by default, and can be configured in `~/.atom/config.json` under `tools.web`.

If you want to disable them, which removes both `web_search` and `web_fetch` from the tool list sent to the LLM, set `tools.web.enable` to `false`:

```json
{
  "tools": {
    "web": {
      "enable": false
    }
  }
}
```

atom uses a shared SSRF guard for built-in web fetches and HTTP/SSE MCP connections. By default it blocks loopback, RFC1918/private ranges, CGNAT/Tailscale ranges, link-local addresses, and cloud metadata endpoints. If you need to allow trusted private ranges, explicitly exempt them from SSRF blocking with `tools.ssrfWhitelist`:

```json
{
  "tools": {
    "ssrfWhitelist": ["100.64.0.0/10"]
  }
}
```

Keep whitelist entries as narrow as possible, such as a single host CIDR (`192.168.1.50/32`). The whitelist is global for the shared SSRF guard; it is not limited to one tool or one MCP server.

HTTP/SSE MCP connections use the same process-wide proxy environment behavior as `web_fetch`: proxied targets use the configured proxy, and URLs excluded by `NO_PROXY` remain DNS-pinned direct connections.

> [!TIP]
> Use `proxy` in `tools.web` to route web requests through a proxy:
> ```json
> { "tools": { "web": { "proxy": "http://127.0.0.1:7890" } } }
> ```
> `web_fetch` applies DNS pinning for direct connections. When an explicit `tools.web.proxy` or a process-wide proxy environment variable applies to the target URL, atom still validates the requested URL locally, but DNS resolution for the outbound fetch happens at the proxy; configure only trusted proxies. URLs excluded by `NO_PROXY` keep the DNS-pinned direct path unless `tools.web.proxy` is configured.

### `tools.web`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | boolean | `true` | Enable or disable all built-in web tools (`web_search` + `web_fetch`) |
| `proxy` | string or null | `null` | Proxy for web requests, for example `http://127.0.0.1:7890`. `web_fetch` DNS pinning applies only to direct connections; proxied fetches rely on the configured proxy as the trusted network exit. |
| `userAgent` | string or null | `null` | User-Agent header for all web requests. If null, a browser one will be used |

### Web Search

atom supports multiple web search providers. Configure in `~/.atom/config.json` under `tools.web.search`.

By default, web search uses `duckduckgo`, and it works out of the box without an API key.

| Provider | Config fields | Env var fallback | Free |
|----------|--------------|------------------|------|
| `brave` | `apiKey` | `BRAVE_API_KEY` | No |
| `tavily` | `apiKey` | `TAVILY_API_KEY` | No |
| `jina` | `apiKey` | `JINA_API_KEY` | Free tier (10M tokens) |
| `kagi` | `apiKey` | `KAGI_API_KEY` | No |
| `olostep` | `apiKey` | `OLOSTEP_API_KEY` | No |
| `bocha` | `apiKey` | `BOCHA_API_KEY` | Free tier (1M calls for startups) |
| `volcengine` | `apiKey` | `VOLCENGINE_SEARCH_API_KEY` or `WEB_SEARCH_API_KEY` | Monthly quota, then paid |
| `keenable` | `apiKey` (optional) | `KEENABLE_API_KEY` | Yes (no key needed; key raises limits) |
| `searxng` | `baseUrl` | `SEARXNG_BASE_URL` | Yes (self-hosted) |
| `duckduckgo` (default) | — | — | Yes |

**Brave:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "${BRAVE_API_KEY}"
      }
    }
  }
}
```

**Tavily:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "tavily",
        "apiKey": "${TAVILY_API_KEY}"
      }
    }
  }
}
```

**Jina** (free tier with 10M tokens):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "jina",
        "apiKey": "${JINA_API_KEY}"
      }
    }
  }
}
```

**Kagi:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "kagi",
        "apiKey": "${KAGI_API_KEY}"
      }
    }
  }
}
```

**Olostep:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "olostep",
        "apiKey": "${OLOSTEP_API_KEY}"
      }
    }
  }
}
```

You can also set `OLOSTEP_API_KEY` in the environment instead of storing it in config.

**Bocha** (AI-optimized search, free tier available):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "bocha",
        "apiKey": "${BOCHA_API_KEY}"
      }
    }
  }
}
```

Create your API key at [open.bochaai.com](https://open.bochaai.com).
Bocha returns structured results optimized for AI consumption, with optional summaries.
You can set `BOCHA_API_KEY` in the environment instead of storing it in config.

**Volcengine Search:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "volcengine",
        "apiKey": "${VOLCENGINE_SEARCH_API_KEY}"
      }
    }
  }
}
```

You can also set `WEB_SEARCH_API_KEY` for compatibility with the Volcengine web-search skill. Create the key in the [Volcengine web search console](https://console.volcengine.com/search-infinity/web-search), then copy it from [API keys](https://console.volcengine.com/search-infinity/api-key). Volcengine Ark keys are separate and do not work for this search provider.

**Keenable** (works without an API key on the free tier):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "keenable"
      }
    }
  }
}
```

Keenable search works out of the box with no account, via its token-less public endpoint (free tier, limited to 1,000 requests/hour). Set `apiKey` (or `KEENABLE_API_KEY`) from [keenable.ai](https://keenable.ai) to remove the hourly limit.

**Serper** (Google Search API):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "serper",
        "apiKey": "${SERPER_API_KEY}"
      }
    }
  }
}
```

Create a key at [serper.dev](https://serper.dev). You can also set `SERPER_API_KEY` in the environment instead of storing it in config.

**SearXNG** (self-hosted, no API key needed):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "searxng",
        "baseUrl": "https://searx.example"
      }
    }
  }
}
```

**DuckDuckGo** (zero config):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

#### `tools.web.search`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `provider` | string | `"duckduckgo"` | Search backend: `brave`, `tavily`, `jina`, `kagi`, `olostep`, `bocha`, `volcengine`, `keenable`, `serper`, `searxng`, `duckduckgo` |
| `apiKey` | string | `""` | API key for API-backed search providers |
| `baseUrl` | string | `""` | Base URL for SearXNG |
| `maxResults` | integer | `5` | Results per search (1–10) |

### Web Fetch

> [!TIP]
> If you are having issues with JS proof-of-work or Cloudflare captchas, set a random user agent and disable Jina Reader:
> ```json
> { "tools": { "web": { "userAgent": "Not-A-Browser", "fetch": { "useJinaReader": false } } } }
> ```

atom by default uses [Jina Reader](https://jina.ai/reader/), a third-party API, to convert arbitrary pages into Markdown format for easy digestion by the LLM, with a local fallback based on [readability-lxml](https://github.com/buriy/python-readability) if the former fails.

If you want to always use the local conversion, you can force it using:

```json
{
  "tools": {
    "web": {
      "fetch": {
        "useJinaReader": false
      }
    }
  }
}
```

#### `tools.web.fetch`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `useJinaReader` | boolean | `true` | If true, Jina Reader will be preferred over the local conversion |

## Image Generation

Image generation is configured under `tools.imageGeneration` and uses credentials from the selected provider's `providers.<name>` block.

See [Image Generation](./image-generation.md) for provider examples, artifact storage, and troubleshooting.

## MCP (Model Context Protocol)

> [!TIP]
> The config format is compatible with Claude Desktop / Cursor. You can copy MCP server configs directly from any MCP server's README.

atom supports [MCP](https://modelcontextprotocol.io/) — connect external tool servers and use them as native agent tools.

Add MCP servers to your `config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

MCP servers can run locally over stdio or connect remotely over HTTP:

| Connection | Config | Example |
|------|--------|---------|
| **Stdio** | `command` + `args` | Local process via `npx` / `uvx` |
| **Streamable HTTP / SSE** | `url` + `headers` (optional) | Remote endpoint (`https://mcp.example.com/mcp`) |

Remote HTTP servers may use browser OAuth instead of static headers. Add
`auth: "oauth"` to the server entry:

```json
{
  "tools": {
    "mcpServers": {
      "notion": {
        "type": "streamableHttp",
        "url": "https://mcp.notion.com/mcp",
        "auth": "oauth"
      }
    }
  }
}
```

OAuth tokens and dynamic client registration data are stored in the atom data
directory under `auth/mcp.json`; they are not written to `config.json`. Removing
the MCP server from the config also removes its saved OAuth credentials.

> [!IMPORTANT]
> There is currently no interactive way to complete the browser authorization.
> The flow was started from the removed browser client, so a server marked
> `auth: "oauth"` connects only when `auth/mcp.json` already holds valid
> credentials; otherwise the server is skipped at startup with a warning.
> Gateway startup never opens a browser or registers a new OAuth client on its
> own. For a remote MCP server you can reach with a static token, prefer `headers`.

> [!IMPORTANT]
> HTTP/SSE MCP URLs are validated before probing or connecting, and every outgoing MCP HTTP request—including OAuth metadata, client registration, token exchange, and redirects—is validated again. `localhost`, `127.0.0.1`, RFC1918/private IPs, CGNAT/Tailscale ranges, link-local addresses, and cloud metadata endpoints are blocked by default. This can break previously working local or private HTTP MCP configs until the endpoint is explicitly allowed with `tools.ssrfWhitelist`, preferably with a single-host CIDR such as `127.0.0.1/32`, `::1/128`, or `192.168.1.50/32`. Stdio MCP servers are not affected.

Use `toolTimeout` to override the default 30s per-call timeout for slow servers:

```json
{
  "tools": {
    "mcpServers": {
      "my-slow-server": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

Use `enabledTools` to register only a subset of tools from an MCP server:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file", "mcp_filesystem_write_file"]
      }
    }
  }
}
```

`enabledTools` accepts either the raw MCP tool name (for example `read_file`) or the wrapped atom tool name (for example `mcp_filesystem_write_file`).

- Omit `enabledTools`, or set it to `["*"]`, to register all capabilities (tools, resources, and prompts).
- Set `enabledTools` to `[]` to register no tools from that server. Resources and prompts are also skipped, since they have no per-name filter.
- Set `enabledTools` to a non-empty list of names to register only those tools — resources and prompts are not registered.

MCP tools are automatically discovered and registered on startup. The LLM can use them alongside built-in tools — no extra configuration needed.


## Security

> [!TIP]
> For production deployments, set both `"restrictToWorkspace": true` and `"tools.exec.sandbox": "bwrap"` in your config. `restrictToWorkspace` enables atom's application-level workspace guards; `tools.exec.sandbox` provides process-level isolation for shell commands.

For API keys, tokens, and other secrets, see [Environment Variables for Secrets](#environment-variables-for-secrets) — avoid storing them directly in `config.json`.

> [!NOTE]
> When a restricted chat session selects a project outside the configured agent
> workspace, that project becomes the normal file and shell boundary. Atom
> adds capability-specific, read-only access for built-in skills, the agent
> workspace's `skills/` directory, and the exact agent
> `memory/history.jsonl` file. Neighboring memory/profile files and all
> cross-workspace writes remain denied. Agent-owned `SOUL.md` and `USER.md` are
> assembled into model context directly; this does not grant file tools broader
> access to the agent workspace.

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `false` | When `true`, enables atom's application-level workspace guards for workspace-aware tools. File tools resolve paths under the active workspace; selected internal roots can be added as read-only or explicitly write-enabled roots, and media uploads are read-only by default. Shell execution rejects workspace-external `working_dir` values and applies best-effort command path checks, but this is not an OS sandbox. |
| `tools.exec.sandbox` | `""` | Sandbox backend for shell commands. Set to `"bwrap"` to wrap exec calls in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox — the process can only see the workspace (read-write) and media directory (read-only); config files and API keys are hidden. Automatically enables workspace restriction for file tools. **Linux only** — requires `bwrap` installed (`apt install bubblewrap`). Not available on macOS (bwrap depends on Linux kernel namespaces). |
| `tools.exec.enable` | `true` | When `false`, the shell `exec` tool is not registered at all. Use this to completely disable shell command execution. |
| `tools.exec.timeout` | `60` | Default hard timeout in seconds for shell commands. Config values may exceed the per-call tool cap; set `0` to disable the hard timeout for trusted long-running commands. |
| `tools.exec.pathPrepend` | `""` | Extra directories to prepend to `PATH` when running shell commands. Use this when configured tools should win executable lookup precedence, such as a Python virtual environment's `bin` or `Scripts` directory. |
| `tools.exec.pathAppend` | `""` | Extra directories to append to `PATH` when running shell commands (e.g. `/usr/sbin` for `ufw`). |
| `tools.exec.sandboxRoBinds` | `[]` | Extra absolute paths to read-only bind into the `"bwrap"` sandbox with `--ro-bind-try`, such as `/home/user/.local/bin` or `/home/user/.cargo/bin` when those paths are also in `pathPrepend`/`pathAppend`. These roots are also accepted by the shell absolute-path guard only while bwrap is active. Bind only directories whose contents are safe for agent commands to read; paths equal to or containing the active workspace are ignored so they cannot uncover its masked parent directory. |
| `tools.exec.sandboxRwBinds` | `[]` | Extra absolute paths to read-write bind into the `"bwrap"` sandbox with `--bind-try`, for trusted tool caches or scratch directories. Use sparingly: paths listed here are intentionally writable by shell commands inside the sandbox. Paths equal to or containing the active workspace are ignored. |
| `tools.ssrfWhitelist` | `[]` | CIDR ranges exempted from the shared SSRF guard used by web fetches and HTTP/SSE MCP connections. Prefer exact host CIDRs such as `192.168.1.50/32`; broad ranges increase SSRF exposure. |
| `channels.*.allowFrom` | omitted | Access control per channel. Omit to use pairing-only mode; set `["*"]` to allow everyone; or list specific user IDs. See [Pairing](#pairing) for details. |


## Pairing

Pairing lets users get access to the bot through a simple code exchange — no config editing required. It covers new users as well as approved users connecting from an additional channel.

### How it works

1. A user sends a DM to the bot on a pairing-capable channel where they aren't yet approved. Telegram is pairing-capable.
2. The bot replies with a pairing code (like `ABCD-EFGH`) and tells them to forward it to you.
3. You approve the code:

```text
/pairing approve ABCD-EFGH
```

4. The user can now chat with the bot normally.

Pairing only works in **DMs** — unapproved users in group chats are silently ignored.

### Pairing-only mode

By default, if you don't set `allowFrom`, pairing-capable channels can issue a pairing code when an unapproved user DMs the bot. This means you can skip `allowFrom` entirely and manage access through pairing:

```json
{
  "channels": {
    "telegram": {
      "enabled": true
    }
  }
}
```

If you prefer to allow everyone without approval:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "allowFrom": ["*"]
    }
  }
}
```

### Managing access

| Command | What it does |
|---------|-------------|
| `/pairing` | Show all pending pairing requests |
| `/pairing approve <code>` | Approve a request — the sender can now chat |
| `/pairing deny <code>` | Reject a pending request |
| `/pairing revoke <user_id>` | Remove a previously approved user from the current channel |
| `/pairing revoke <channel> <user_id>` | Remove a user from a specific channel |

You can find user IDs in the output of `/pairing list`.

From the terminal:

```bash
atom agent -m "/pairing list"
atom agent -m "/pairing approve ABCD-EFGH"
```


## Gateway Heartbeat

The gateway can run a protected heartbeat cron job that periodically checks `HEARTBEAT.md` in the active workspace. This is enabled by default when you run `atom gateway`.

```json
{
  "gateway": {
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800,
      "keepRecentMessages": 8
    }
  }
}
```

If `HEARTBEAT.md` has tasks under `## Active Tasks`, the agent executes them and sends only useful/actionable results to the most recently active chat target. If the file has no active tasks, or the result is routine with nothing useful to report, the heartbeat is skipped silently.

This is intentionally different from user-created cron jobs. A cron job created with the `cron` tool runs as a scheduled turn in its origin chat/session and normally delivers the result back to that channel. Use `HEARTBEAT.md` for recurring background checks that should not notify the user on every run.

The heartbeat job is backed by the same cron service as user-created reminders. It is stored under the active workspace (`<workspace>/cron/jobs.json`) and shows up in `cron(action="list")` as `heartbeat`, but it is system-managed and cannot be removed with the `cron` tool. Disable it through config and restart the gateway if you do not want periodic heartbeat checks.

| Option | Default | Description |
|--------|---------|-------------|
| `gateway.heartbeat.enabled` | `true` | Register the built-in heartbeat cron job on gateway startup. |
| `gateway.heartbeat.intervalS` | `1800` | Seconds between heartbeat checks. |
| `gateway.heartbeat.keepRecentMessages` | `8` | Number of recent heartbeat-session messages to retain after each run. |
| `gateway.restartMode` | `auto` | Restart strategy for `/restart`: `auto` resolves to `exec`, replacing the current process. Use `spawn` to start a replacement process and exit, or `exit` when a supervisor such as systemd owns the restart. |

### Custom heartbeat evaluator prompt

The notification gate runs on a built-in system prompt. Advanced users can override it, but you rarely need to — it's strongly advised to first read the evaluator code and the default `evaluator.md`. To override, drop your prompt at `<workspace>/prompts/evaluator.md`. It must still instruct the model to call the `evaluate_notification` tool; otherwise the gate fails closed and stays silent.


## Subagent Concurrency

By default, atom only allows one spawned subagent at a time. When the limit is reached, the `spawn` tool returns an error so the agent can decide to wait or rearrange its work. This protects local LLM servers from loading multiple KV caches at once. If your provider can handle more parallel work, raise the limit:

```json
{
  "agents": {
    "defaults": {
      "maxConcurrentSubagents": 2
    }
  }
}
```

Subagents also stop immediately when one of their tools returns an execution error. That default keeps failures visible to the parent agent. If your subagent workflows use tools that can fail transiently and should be retried or worked around by the model, disable hard-stop behavior:

```json
{
  "agents": {
    "defaults": {
      "failOnToolError": false
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.maxConcurrentSubagents` | `1` | Maximum number of spawned subagents that may run at the same time. Attempts to spawn beyond this limit return an error. |
| `agents.defaults.failOnToolError` | `true` | Stop a spawned subagent when a tool execution fails. Set to `false` to return tool errors to the subagent model so it can recover within the same run. |


## Auto Compact

When a user is idle for longer than a configured threshold, atom **proactively** compresses the older part of the session context into a summary while keeping a recent legal suffix of live messages. This reduces token cost and first-token latency when the user returns — instead of re-processing a long stale context with an expired KV cache, the model receives a compact summary, the most recent live context, and fresh input.

```json
{
  "agents": {
    "defaults": {
      "idleCompactAfterMinutes": 15,
      "idleCompactCheckIntervalSeconds": 60
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.idleCompactAfterMinutes` | `15` | Minutes of idle time before auto-compaction starts. Set to `0` to disable. The default is close to a typical LLM KV cache expiry window, so stale sessions get compacted before the user returns. |
| `agents.defaults.idleCompactCheckIntervalSeconds` | `60` | Minimum number of seconds between scans for idle sessions. Set to `0` to scan on every idle tick (~1 s). |

`sessionTtlMinutes` remains accepted as a legacy alias for backward compatibility, but `idleCompactAfterMinutes` is the preferred config key going forward.

How it works:
1. **Idle detection**: On each idle tick (~1 s), checks whether an idle-session scan is due. By default, the full scan runs at most once per minute.
2. **Background compaction**: Idle sessions summarize the older live prefix via LLM and keep the most recent legal suffix (currently 8 messages).
3. **Summary injection**: When the user returns, the summary is injected as runtime context (one-shot, not persisted) alongside the retained recent suffix.
4. **Restart-safe resume**: The summary is also mirrored into session metadata so it can still be recovered after a process restart.

> [!NOTE]
> Mental model: "summarize older context, keep the freshest live turns, **and overwrite the session file with the compact form.**" It is not a full `session.clear()`, but it is a write — not a soft cursor move.
>
> Concretely, auto compact rewrites `sessions/<key>.jsonl` in place: older messages (including their structured `tool_calls` / `tool_call_id` / `reasoning_content`) are replaced by just the retained recent suffix (currently 8 messages), while the archived prefix is preserved only as a plain-text summary appended to `memory/history.jsonl` (or a `[RAW] ...` flattened dump if LLM summarization fails). The original structured JSON of those turns is no longer recoverable from the session file.
>
> This differs from the **token-driven soft consolidation** that fires when a prompt exceeds the context budget: that path only advances an internal `last_consolidated` cursor and leaves the session file untouched, so the raw tool-call trail stays on disk and can still be replayed or audited. If you rely on that trail for debugging or auditing, set `idleCompactAfterMinutes` to `0` and let only the token-driven path run.

## Timezone

Time is context. Context should be precise.

By default, atom uses `UTC` for runtime time context. If you want the agent to think in your local time, set `agents.defaults.timezone` to a valid [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones):

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Shanghai"
    }
  }
}
```

This affects runtime time strings shown to the model, such as runtime context. It also becomes the default timezone for cron schedules when a cron expression omits `tz`, and for one-shot `at` times when the ISO datetime has no explicit offset.

Common examples: `UTC`, `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Europe/Berlin`, `Asia/Tokyo`, `Asia/Shanghai`, `Asia/Singapore`, `Australia/Sydney`.

> Need another timezone? Browse the full [IANA Time Zone Database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

## Unified Session

By default, each channel × chat ID combination gets its own session. If you use atom across multiple channels (e.g. Telegram + CLI + the API server) and want them to share the same conversation, enable `unifiedSession`:

```json
{
  "agents": {
    "defaults": {
      "unifiedSession": true
    }
  }
}
```

When enabled, all incoming messages — regardless of which channel they arrive on — are routed into a single shared session. Switching from Telegram to the CLI (or any other channel) continues the same conversation seamlessly.

| Behavior | `false` (default) | `true` |
|----------|-------------------|--------|
| Session key | `channel:chat_id` | `unified:default` |
| Cross-channel continuity | No | Yes |
| `/new` clears | Current channel session | Shared session |
| `/stop` finds tasks | By channel session | By shared session |
| Existing `session_key_override` (e.g. Telegram thread) | Respected | Still respected — not overwritten |

> This is designed for single-user, multi-device setups. It is **off by default** — existing users see zero behavior change.

## Disabled Skills

atom ships with built-in skills, and your workspace can also define custom skills under `skills/`. If you want to hide specific skills from the agent, set `agents.defaults.disabledSkills` to a list of skill directory names:

```json
{
  "agents": {
    "defaults": {
      "disabledSkills": ["github", "weather"]
    }
  }
}
```

Disabled skills are excluded from the main agent's skill summary, from always-on skill injection, and from subagent skill summaries. This is useful when some bundled skills are unnecessary for your deployment or should not be exposed to end users.

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.disabledSkills` | `[]` | List of skill directory names to exclude from loading. Applies to both built-in skills and workspace skills. |

### Agent Plugins v1

atom discovers [Agent Plugins](https://agent-plugins.org/) under `<workspace>/plugins/`; a v1 package has `plugin.json` and may add `mcp.json`, `skills/<name>/SKILL.md`, or both. Agent Plugins are the common package and activation boundary for installable capabilities; they do not replace native providers, channels, tools, standalone workspace skills, or directly configured MCP servers.

Directory presence means installed; activation is explicit, recorded by an `enabled` marker under the config directory's `plugin-data/<workspace-id>/<plugin-name>/`. Skills use progressive loading and `$skill-name` invocation, with workspace > plugin > built-in precedence.
Enabled `stdio` servers receive contained `PLUGIN_ROOT` and isolated `PLUGIN_DATA` paths; explicit
`tools.mcpServers` entries win collisions. Invalid or escaping components are ignored.
An enabled package is treated as immutable: changing any packaged file disables it until the user
reviews and enables it again. Runtime state belongs under `PLUGIN_DATA`, not the package root.

Enabled plugins run as the atom user; permissions are descriptive, not an OS sandbox. The optional `extensions.dev.atom.logo` accepts a contained PNG, JPEG, or WebP up to 256 KiB.

CLI Apps use the same skills-only package layout while their installer manages executables, updates, and removal. Future catalogs can place packages before using this activation path.

## Tool Hint Max Length

Tool hints are the short progress messages shown when the agent calls tools (e.g. `$ cd …/project && npm test`). By default, these are truncated at 40 characters, which can make long commands hard to read.

Set `agents.defaults.toolHintMaxLength` to control the truncation threshold:

```json
{
  "agents": {
    "defaults": {
      "toolHintMaxLength": 120
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.toolHintMaxLength` | `40` | Maximum characters for tool hint display. Range: 20–500. Higher values show more of the command or path; lower values keep hints compact. |
