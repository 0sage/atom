# Provider Cookbook

This page is for cases where you already know what you want to connect and need a pasteable setup. Each recipe shows what to set, what to run, and what a failure usually means.

If this is your first install and terminal commands are new to you, start with [`start-without-technical-background.md`](./start-without-technical-background.md). If you want the field-by-field explanation, read [`providers.md`](./providers.md) and then [`configuration.md#providers`](./configuration.md#providers).

Most examples below are snippets to merge into `~/.atom/config.json`. Keep any existing sections you still need, and replace placeholder keys such as `${ANTHROPIC_API_KEY}` with environment-variable references or real values only on your own machine.

Recipes are examples, not rankings. Pick the recipe that matches the credential, endpoint, and model ID you already intend to use.

## Choose a Recipe

Match the recipe to the credential or endpoint you already have:

| What you have | Recipe | Must match |
|---|---|---|
| An Anthropic API key and Anthropic model ID | [Anthropic Direct](#recipe-anthropic-direct) | `ANTHROPIC_API_KEY`, `provider: "anthropic"`, and an Anthropic model ID |
| An OpenAI platform API key and OpenAI model ID | [OpenAI Direct](#recipe-openai-direct) | `OPENAI_API_KEY`, `provider: "openai"`, and an OpenAI model available to that account |
| A Groq API key | [Groq](#recipe-groq) | `GROQ_API_KEY`, `provider: "groq"`, and a Groq model ID |
| An OpenAI-compatible `/v1` endpoint that is not a built-in provider | [Custom OpenAI-Compatible Provider](#recipe-custom-openai-compatible-provider) | `apiBase`, optional API key, and the model ID served by that endpoint |
| A local OpenAI-compatible server (Ollama, vLLM, LM Studio) | [Local OpenAI-Compatible Server](#recipe-local-openai-compatible-server) | Local `/v1` base URL, any required key, and served model name |
| A provider endpoint behind a proxy or in another region | [Override a Built-In Base URL](#recipe-override-a-built-in-base-url) | The built-in provider block plus `apiBase` |
| A primary model plus one or more backups | [Fallback Presets](#recipe-fallback-presets) | Named presets in `modelPresets`, referenced from `agents.defaults.fallbackModels` |
| A working agent and a Langfuse project | [Langfuse Tracing](#recipe-langfuse-tracing) | Langfuse env vars in the same process environment that starts atom |

## How to Use a Recipe

1. Install atom and run `atom onboard` once so `~/.atom/config.json` exists. Use `atom onboard --wizard` if you prefer prompts over hand-editing JSON.
2. Put secrets in environment variables when possible.
3. Merge the recipe snippet into `~/.atom/config.json`.
4. Run `atom status`.
5. Run `atom agent -m "Hello!"`.
6. If the CLI works, then start the gateway and connect chat apps.

The active model should normally come from `agents.defaults.modelPreset`, and that name should point to an entry in `modelPresets`. Direct `agents.defaults.provider` and `agents.defaults.model` still work for older configs, but presets are easier to switch and easier to reuse as fallbacks.

## Secret Setup

Environment variables keep API keys out of the config file.

Use the variable name shown by the recipe you picked. The commands below use `ANTHROPIC_API_KEY` only as an example; an OpenAI direct recipe uses `OPENAI_API_KEY`, a Groq recipe uses `GROQ_API_KEY`, and a custom endpoint can use any variable name you reference in `config.json`.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
atom agent -m "Hello!"
```

Environment variables set this way apply only to the current terminal. For long-running services such as systemd, a LaunchAgent, or a remote shell, set the variables in that service environment before starting atom.

## Recipe: Anthropic Direct

This recipe applies when your key comes from Anthropic and your model name is an Anthropic model ID.

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Anthropic",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Verify:

```bash
ANTHROPIC_API_KEY="sk-ant-..." atom agent -m "Hello!"
```

If you copied a model name such as `anthropic/claude-sonnet-4.5`, that is a vendor-prefixed path from a gateway catalog. Against `provider: "anthropic"`, use the plain Anthropic model ID instead.

Do not configure Anthropic-compatible endpoints as arbitrary custom provider names; named custom providers use the OpenAI-compatible request format. Use `providers.anthropic.apiBase` — see [Override a Built-In Base URL](#recipe-override-a-built-in-base-url).

## Recipe: OpenAI Direct

This recipe applies when you have an OpenAI API key and want to call OpenAI directly.

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "OpenAI",
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Verify:

```bash
OPENAI_API_KEY="sk-..." atom agent -m "Hello!"
```

If your shell cannot use inline environment variables, set `OPENAI_API_KEY` first and then run `atom agent -m "Hello!"`. `apiType` is only valid on `providers.openai`; remove it unless you are forcing a documented OpenAI API surface (`chat_completions` or `responses`).

## Recipe: Groq

This recipe applies when you have a Groq API key. Groq is also the default backend for voice transcription.

```json
{
  "providers": {
    "groq": {
      "apiKey": "${GROQ_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Groq",
      "provider": "groq",
      "model": "llama-3.3-70b-versatile",
      "maxTokens": 4096,
      "contextWindowTokens": 131072,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Verify:

```bash
GROQ_API_KEY="gsk_..." atom agent -m "Hello!"
```

atom defaults the base URL to `https://api.groq.com/openai/v1`, so `apiBase` is only needed for a proxy. To use the same key for voice transcription, see [`configuration.md#transcription`](./configuration.md#transcription-settings).

## Recipe: Custom OpenAI-Compatible Provider

This recipe applies to an OpenAI-compatible service that is not one of the built-in providers.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Custom",
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Verify the endpoint before blaming atom:

```bash
curl -sS https://api.example.com/v1/models
atom agent -m "Hello!"
```

`apiBase` is the HTTP base URL, not the model name. Include the version path when the service expects it, such as `/v1`. If the service requires a non-empty key but does not validate it, use a placeholder such as `"apiKey": "EMPTY"`.

For multiple custom endpoints, do not overload the single `custom` block. Name each endpoint under `providers` and reference that same name from the preset:

```json
{
  "providers": {
    "workProxy": {
      "apiKey": "${WORK_PROXY_API_KEY}",
      "apiBase": "https://proxy.example.com/v1"
    },
    "lab-local": {
      "apiBase": "http://127.0.0.1:8000/v1"
    }
  },
  "modelPresets": {
    "work": {
      "label": "Work proxy",
      "provider": "workProxy",
      "model": "gpt-4o-mini",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "lab": {
      "label": "Lab local",
      "provider": "lab-local",
      "model": "served-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "work"
    }
  }
}
```

These custom names behave like direct OpenAI-compatible providers: `apiBase` is required, `apiKey` is optional when the endpoint allows anonymous or placeholder credentials, and `apiType` should be left unset. Pick a name that does not collide with a built-in provider name in any capitalization (`anthropic`, `openai`, `groq`, `custom`) — atom rejects the config if it does. They do not support Anthropic-compatible endpoints; use the `anthropic` provider with `apiBase` for that case.

If your endpoint documents a nonstandard thinking toggle, set `providers.<name>.thinkingStyle` to `thinking_type`, `enable_thinking`, or `reasoning_split` so `reasoningEffort` maps onto that provider's request body.

## Recipe: Local OpenAI-Compatible Server

This recipe applies when a local server exposes an OpenAI-compatible `/v1` API. Ollama, vLLM, and LM Studio all work through the `custom` provider — atom no longer ships dedicated blocks for them.

For Ollama:

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
      "label": "Local",
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

For vLLM, which usually wants a placeholder key:

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "local": {
      "label": "Local",
      "provider": "custom",
      "model": "served-model-name",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local"
    }
  }
}
```

For LM Studio, use its local base URL — typically port `1234`:

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "local": {
      "label": "LM Studio",
      "provider": "custom",
      "model": "local-model",
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

To run more than one local server at once, give each its own named provider key as shown in the previous recipe.

Verify:

```bash
curl -sS http://localhost:11434/v1/models
atom agent -m "Hello!"
```

atom recognizes localhost and private-range base URLs and tunes connection keepalive for them; a `502` or connection-refused failure against such a URL adds a local-endpoint reachability hint. If you see `connection refused`, the server is not running or `apiBase` points at the wrong port. If every response is slow, try a smaller local model or lower `contextWindowTokens`.

If direct Ollama responses are fast but tool-using atom turns repeatedly evaluate
thousands of prompt tokens, the model's chat template may be moving its tool
definitions between requests. See
[Improve Ollama Tool-Calling Prompt Cache Reuse](./guides/configure-ollama-prompt-cache.md)
for a diagnostic procedure and an optional model-specific workaround.

## Recipe: Override a Built-In Base URL

This recipe applies when you reach a built-in provider through a company proxy, gateway, or regional endpoint. Keep the built-in provider name so the correct request format is used, and add `apiBase`.

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    },
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Anthropic proxy",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

For Anthropic, a trailing `/v1` is normalized away, so `https://proxy.example.com/anthropic` and `https://proxy.example.com/anthropic/v1` behave identically. For OpenAI-compatible endpoints, include the version path the service expects.

If only one provider needs to route through an HTTP proxy, use `providers.<name>.proxy` instead of process-wide `HTTP_PROXY`. That field is supported for the OpenAI-compatible providers (`openai`, `groq`, `custom`, named custom keys); `anthropic` rejects it, so use `apiBase` for that provider.

## Recipe: Fallback Presets

This recipe applies when one provider sometimes rate-limits, one model is expensive, or you want a local backup.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.1
    },
    "local": {
      "label": "Local",
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": ["deep", "local"]
    }
  }
}
```

`fallbackModels` belongs under `agents.defaults`. String entries are preset names, not raw model names. atom tries the active preset first, then the fallback presets in order.

Keep fallback candidates realistic. If the local fallback has a smaller context window, atom must build context that fits the smallest window in the active chain.

## Recipe: Langfuse Tracing

This recipe applies after the agent works and you want observability for OpenAI-compatible provider calls.

Install the optional package in the same Python environment that runs atom:

```bash
atom plugins enable langfuse
```

Set the environment variables before starting atom:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
atom agent -m "Hello!"
```

Langfuse is not a model provider in `config.json`. It is configured through environment variables and traces supported OpenAI-compatible provider calls (`openai`, `groq`, `custom`, named custom keys). The native `anthropic` provider does not use that client path and may not produce Langfuse OpenAI-wrapper traces.

## Recipe: Switch Models at Runtime

Use this after you have more than one preset and are chatting through a supported channel.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 200000
    },
    "local": {
      "label": "Local",
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 2048,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast"
    }
  }
}
```

In chat:

```text
/model
/model local
/model fast
```

`/model` stores the selection in the current session without rewriting `config.json`.
The selection survives restarts, does not affect other sessions, and an in-progress
turn keeps using the model it started with.

## Quick Failure Map

| Symptom | Usually means | First check |
|---|---|---|
| `401`, `unauthorized`, or `invalid API key` | The key is missing, wrong, expired, or under the wrong provider | Print or re-set the environment variable in the same terminal or service |
| `model not found` | The model ID does not belong to the selected provider | Compare `modelPresets.<name>.provider` and `modelPresets.<name>.model` |
| `connection refused` | Local server is not running or `apiBase` has the wrong port/path | Run `curl <apiBase>/models` |
| `requires api_base in config` | A `custom` or named custom provider has no `apiBase` | Add the full base URL including any version path |
| `provider not found` | Provider name is misspelled or uses the config key instead of the registry name | Use `anthropic`, `openai`, `groq`, or `custom` |
| `conflicts with built-in provider` | A named custom provider reuses a built-in name | Rename the custom key to something unique |
| `proxy is only supported for` | `proxy` was set on `anthropic` | Use `apiBase` instead |
| Langfuse shows no traces | Env vars are missing, `langfuse` is not installed in the active Python environment, or the provider path is native | Run `python -m pip show langfuse` and restart atom from the same environment |

## Next References

| Need | Read |
|---|---|
| Field meanings and provider resolution | [`providers.md`](./providers.md) |
| Full schema and provider table | [`configuration.md#providers`](./configuration.md#providers) |
| Langfuse details | [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) |
| First-run diagnosis | [`troubleshooting.md`](./troubleshooting.md) |
