# Providers and Models

Use this page when the first reply fails because of provider/model mismatch, or when you want to adapt the concrete setup example to a different provider. If you already know which provider you want and only need a pasteable setup, use [`provider-cookbook.md`](./provider-cookbook.md).

For a first local setup, `atom onboard --wizard` adds provider credentials, creates a model preset, and selects the active model. Use the JSON below for manual deployments, local endpoints, provider-specific fields, or diagnosis.

atom ships five providers:

| Provider | Backend | Credential |
|---|---|---|
| `anthropic` | Native Anthropic Messages API | `apiKey`, optional `apiBase` override |
| `openai` | OpenAI Chat Completions / Responses | `apiKey`, optional `apiBase` override |
| `openai-codex` | OpenAI Responses via the Codex endpoint | ChatGPT sign-in, no `apiKey` — see [OpenAI Codex](#openai-codex) |
| `groq` | OpenAI-compatible | `apiKey`; also the default voice-transcription provider |
| `custom` | OpenAI-compatible | `apiBase` required, `apiKey` optional |

Any other OpenAI-compatible endpoint works through `custom`, or through your own named provider key — see [Custom OpenAI-Compatible Endpoint](#custom-openai-compatible-endpoint).

For every setup, answer three questions:

1. Which provider owns the credential or endpoint?
2. What model name does that provider expect?
3. Does the provider need `apiKey`, `apiBase`, cloud credentials, or only a local server URL?

Prefer a named `modelPresets` entry for the model/provider pair, then select it with `agents.defaults.modelPreset`. Direct `agents.defaults.provider` and `agents.defaults.model` still work for existing configs, but presets make runtime `/model` switching and fallback chains clearer. Pin `provider` inside the preset while setting up; you can switch back to `"auto"` later.

## Choose a Provider Without Guessing

Start from the service or endpoint you actually control:

| If you have... | Configure... |
|---|---|
| An Anthropic API key | `providers.anthropic.apiKey`, then a preset with `provider: "anthropic"` and a Claude model ID. |
| An OpenAI API key | `providers.openai.apiKey`, then a preset with `provider: "openai"` and a model ID your account can reach. |
| A Groq API key | `providers.groq.apiKey`, then a preset with `provider: "groq"`. |
| A company proxy or regional endpoint | The matching provider block plus `apiBase`, or a named custom provider key. |
| Any other OpenAI-compatible endpoint, including a local server | `providers.custom` (or your own named key) with `apiBase`. |
| No provider yet | Pick one outside atom based on account access, pricing, regional availability, privacy requirements, and the model IDs you need. Then come back with its key and model ID. |

## Minimal Shape

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
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

The provider config gives atom credentials and endpoint details. The model preset names the provider/model pair. The agent defaults choose which named preset to use for normal turns. Replace the example provider and model together; mixing an API key from one provider with a model ID from another is the most common first-run failure.

## Provider, Model, API Key, and Base URL

These fields answer different questions:

| Field | Where it lives | Meaning |
|---|---|---|
| `provider` | `modelPresets.<name>.provider` | Which atom provider adapter should send the request. |
| `model` | `modelPresets.<name>.model` | The model ID expected by that provider. |
| `apiKey` | `providers.<provider>.apiKey` | Credential for that provider. Use `${ENV_VAR}` for secrets. |
| `apiBase` | `providers.<provider>.apiBase` | HTTP base URL of the provider endpoint. |
| `proxy` | `providers.<provider>.proxy` | Optional HTTP proxy for this provider only. Supported for OpenAI-compatible providers. |

You usually omit `apiBase` for `anthropic`, `openai`, and `groq` because atom knows their default endpoints. Set `apiBase` to point one of those at a proxy or regional endpoint, and always set it for `custom`, named custom providers, and local OpenAI-compatible servers. Include the API version path when the endpoint requires it, for example `https://api.example.com/v1` or `http://localhost:11434/v1`.

Use `proxy` when one provider must send HTTP traffic through a proxy without changing process-wide `HTTP_PROXY` / `HTTPS_PROXY`. This is supported for providers that use atom's OpenAI-compatible client: `openai`, `groq`, `custom`, and named custom providers. The native `anthropic` backend rejects `proxy`; set `providers.anthropic.apiBase` instead.

### Why `custom` Exists Alongside `openai`

`providers.openai.apiBase` overrides the endpoint exactly the way `providers.anthropic.apiBase` does, so base-URL override is not what separates the two. Use `custom` (or your own named key) rather than `openai` whenever the endpoint is not OpenAI itself, because `provider: "openai"` also selects OpenAI's request dialect and auth rules:

| | `provider: "openai"` | `provider: "custom"` or a named key |
|---|---|---|
| Token limit field | Sends `max_completion_tokens`, which newer OpenAI models require | Sends `max_tokens`, which Ollama, vLLM, and llama.cpp expect |
| `apiKey` | Required | Optional, so a keyless local server works |
| `apiType` (`chat_completions` / `responses`) | Accepted | Rejected — the Responses API is OpenAI-only |
| Responses API probing | Enabled when the base URL really is `api.openai.com` | Never |
| `provider: "auto"` inference | Matches model names containing `openai` or `gpt` | No keywords; always select it explicitly |

So pointing `openai` at a local server sends the wrong token field and still demands a key. Point `custom` at it instead.

`custom` and a provider key you invent share the same backend, credential rules, and request shape. Use `custom` for a single extra endpoint, and named keys when you need more than one. Two details differ:

- The setup wizard offers `custom` as an explicit choice and always asks for a base URL; named keys are hand-written into `config.json`.
- A named key strips its own prefix from the model ID, so `provider: "myProxy"` with `model: "myProxy/gpt-4o-mini"` sends `gpt-4o-mini` upstream. `custom` does not strip, so `model: "custom/gpt-4o-mini"` is sent as written. Model IDs without a `provider/` prefix behave identically under both.

## Common Provider Patterns

### Anthropic Direct

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Anthropic direct uses the native Anthropic provider, which supports prompt caching.

If you use an Anthropic-compatible proxy or a regional endpoint, keep the provider as `anthropic` and override `apiBase`:

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5"
    }
  }
}
```

A trailing `/v1` is normalized away, so both `https://proxy.example.com/anthropic` and `https://proxy.example.com/anthropic/v1` resolve to the same base.

Arbitrary custom provider names are OpenAI-compatible only; they do not use the Anthropic Messages API request format. For an Anthropic-compatible endpoint, always use `providers.anthropic.apiBase`.

### OpenAI Direct

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "openai",
      "model": "gpt-5",
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

To send OpenAI traffic through a proxy, gateway, or regional endpoint, set `apiBase` and keep `provider: "openai"`:

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    }
  }
}
```

`providers.openai.apiType` may be set when you need to force a specific OpenAI API surface (`chat_completions` or `responses`). Other providers reject `apiType`; leave it unset outside `providers.openai`. Replace the model with a model ID available to your OpenAI account. Direct OpenAI Responses models use [opaque Responses state retention](./configuration.md#responses-state-and-compaction); native compaction is enabled only where the backend supports it. Provider-native features such as OpenAI web search are configured through raw provider request fields under `extraBody`.

### OpenAI Codex

Runs turns against your ChatGPT subscription instead of an API key, using the same OAuth flow the Codex CLI uses. Sign in once:

```bash
atom auth login openai-codex
```

The browser opens, you approve, and the credential is stored at `~/.atom/auth/codex.json` with owner-only permissions. `atom auth status openai-codex` reports whether a credential is present without revealing it; `atom auth logout openai-codex` removes it.

Because there is no API key, `providers.openai_codex` needs no credential field — an empty block is enough, and you may omit it entirely:

```json
{
  "modelPresets": {
    "primary": {
      "provider": "openai_codex",
      "model": "openai-codex/gpt-5.6-sol",
      "maxTokens": 8192,
      "contextWindowTokens": 372000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Models: `openai-codex/gpt-5.6-sol` (frontier), `openai-codex/gpt-5.6-terra` (balanced), `openai-codex/gpt-5.6-luna` (fast). The `openai-codex/` prefix is required — it is what routes the model here rather than to `openai`. A bare `gpt-*` model still belongs to the API-key `openai` provider, and this provider is never chosen as a blind fallback.

This provider is hidden from `atom onboard` because there is no key to prompt for; `atom auth login` is its setup path. It uses [opaque Responses state retention](./configuration.md#responses-state-and-compaction) with the Codex backend's own inline compaction. `proxy`, `extraBody`, and `extraHeaders` are supported; `apiKey`, `apiBase`, and `apiType` are not used.

If atom has no credential of its own but the Codex CLI does, the token in `~/.codex/auth.json` is imported automatically. That is also the recovery path when a refresh fails because OAuth refresh tokens are single-use and the CLI already consumed the current one. `atom auth logout` never touches the CLI's file.

### Groq

Groq is an OpenAI-compatible endpoint and also the default backend for voice transcription.

```json
{
  "providers": {
    "groq": {
      "apiKey": "${GROQ_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "groq",
      "model": "llama-3.3-70b-versatile",
      "maxTokens": 8192,
      "contextWindowTokens": 131072
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

atom defaults its base URL to `https://api.groq.com/openai/v1`. For transcription setup, see [`configuration.md#transcription`](./configuration.md#transcription-settings).

### Custom OpenAI-Compatible Endpoint

The `custom` provider fits one OpenAI-compatible endpoint that is not one of the built-in providers.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "provider-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

`custom` does not infer a default base URL. Set `apiBase`.

This is also the path for a local OpenAI-compatible server such as Ollama, vLLM, or LM Studio — point `apiBase` at the local port and omit `apiKey` if the server does not require one:

```json
{
  "providers": {
    "custom": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  }
}
```

atom detects localhost and private-range base URLs and adjusts connection keepalive accordingly; a 502 or connection-refused error against such a URL adds a local-endpoint reachability hint.

If you have more than one OpenAI-compatible endpoint, give each one its own provider key under `providers` and use that same key in the model preset. The key can be a name that makes sense in your environment, such as `companyProxy`, `tenant-a`, or `dev-local`.

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    },
    "tenant-a": {
      "apiBase": "https://tenant-a.example.com/v1"
    }
  },
  "modelPresets": {
    "company": {
      "provider": "companyProxy",
      "model": "gpt-4o-mini",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    },
    "tenantA": {
      "provider": "tenant-a",
      "model": "served-model-name",
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "company"
    }
  }
}
```

Custom provider keys are treated as direct OpenAI-compatible providers. `apiBase` is required because atom cannot know the endpoint URL. `apiKey` is optional for local servers or private proxies that do not require one. Choose a name that does not conflict with a built-in provider name, in any capitalization: `anthropic`, `openai`, `openai_codex`, `groq`, or `custom`. Do not set `apiType` on custom provider keys; `apiType` is only for `providers.openai`.

If your custom endpoint documents a nonstandard thinking toggle, set `providers.<name>.thinkingStyle` to `thinking_type`, `enable_thinking`, or `reasoning_split`; atom then maps `reasoningEffort` onto that provider-specific request body. Leave it unset for ordinary OpenAI-compatible endpoints.

This named custom provider path is not for Anthropic-compatible endpoints. For Anthropic-compatible proxies, use `providers.anthropic.apiBase` and set the preset provider to `anthropic`.

## Provider Resolution

The recommended path is a named preset selected by `agents.defaults.modelPreset`. The effective model parameters come from:

1. the named `modelPresets` entry referenced by `agents.defaults.modelPreset`;
2. otherwise the implicit `default` preset built from `agents.defaults.model`, `provider`, `maxTokens`, `contextWindowTokens`, `temperature`, and related fields.

Provider selection follows this practical rule:

- Explicit `provider` in the active preset or implicit default config wins.
- `provider: "auto"` tries model-name keywords, configured keys, and base URLs.
- Custom and named custom providers should normally be explicit, because generic model names such as `llama3.2` do not contain provider keywords.

### Model Name Prefixes

`family/model-name` does not always select provider `family`. Prefix-based provider inference only runs when the active provider is `"auto"`.

- Explicit provider wins: `provider: "custom"` with `model: "anthropic/claude-sonnet-4.5"` calls your custom endpoint, not Anthropic.
- With `provider: "auto"`, a prefix matching a configured built-in or named custom provider can select that provider. Named custom prefixes are stripped before request, so `companyProxy/gpt-4o-mini` is sent upstream as `gpt-4o-mini`.
- With an explicit named custom provider, the model is sent as written; `provider: "companyProxy"` with `model: "openai/gpt-4o-mini"` sends `openai/gpt-4o-mini` to `companyProxy`.

Pin `provider` in presets when the model ID carries a vendor prefix such as `anthropic/claude-sonnet-4.5`.

## Model Presets

Model presets are the recommended model configuration surface. Use them when you want named model choices, runtime `/model` switching, or reusable fallback targets.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast"
    }
  }
}
```

The preset name `default` is reserved for the implicit `agents.defaults` settings. Do not define `modelPresets.default`; use `/model default` to return to the direct `agents.defaults.*` fields in older configs.

## Fallback Models

Fallbacks are useful for transient provider failures, rate limits, or model availability issues. Keep fallbacks compatible with the task size and tool use. Prefer fallback presets so each candidate has a name and a complete provider, model, generation, and context-window configuration.

```json
{
  "modelPresets": {
    "fast": {
      "label": "Fast",
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "label": "Deep",
      "provider": "anthropic",
      "model": "claude-opus-4-5",
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1
    },
    "localSmall": {
      "label": "Local Small",
      "provider": "custom",
      "model": "llama3.2",
      "maxTokens": 4096,
      "contextWindowTokens": 32768,
      "temperature": 0.2
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

String entries in `fallbackModels` are preset names, not raw model names. atom tries them in order after the active preset. Each fallback preset uses its own `provider`, `model`, `maxTokens`, `contextWindowTokens`, `temperature`, and optional `reasoningEffort`.

Use inline fallback objects only when a model is not worth naming as a preset:

```json
{
  "modelPresets": {
    "fast": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "maxTokens": 4096,
      "contextWindowTokens": 65536
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

`fallbackModels` belongs under `agents.defaults`, not inside each preset. If fallback candidates use smaller context windows, atom builds context using the smallest window in the active chain so every candidate can receive the same prompt. See [`configuration.md#model-fallbacks`](./configuration.md#model-fallbacks) for failure conditions.

## Quick Checks

Run these before debugging a chat app:

```bash
atom status
atom agent -m "Hello!"
```

If `atom agent -m "Hello!"` fails:

| Symptom | Likely cause |
|---|---|
| 401, unauthorized, invalid API key | Key is missing, expired, copied with whitespace, or stored under the wrong provider |
| model not found | Model ID does not exist for the selected provider |
| connection refused | Local server is not running or `apiBase` points to the wrong port |
| provider not found | The active preset uses a misspelled provider; use registry names `anthropic`, `openai`, `openai_codex`, `groq`, or `custom` |
| requires api_base in config | A `custom` or named custom provider is missing `apiBase` |
| works in CLI but not chat app | Provider is fine; debug gateway/channel setup in [`chat-apps.md`](./chat-apps.md) or [`troubleshooting.md`](./troubleshooting.md) |

For the complete provider table and advanced provider-specific notes, see [`configuration.md#providers`](./configuration.md#providers).
