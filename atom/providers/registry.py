"""
Provider Registry — single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add a field to ProvidersConfig in config/schema.py.
  Done. Env vars, config matching, status display all derive from here.

Order matters — it controls match priority and fallback. Gateways first.
Every entry writes out all fields so you can copy-paste as a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic.alias_generators import to_snake


@dataclass(frozen=True)
class ProviderModelSpec:
    """A curated model exposed by providers without a model-list endpoint."""

    id: str
    label: str = ""
    description: str = ""
    context_window: int | None = None


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "anthropic"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""  # shown in `atom status`
    model_catalog: str = "auto"  # model-list source
    builtin_models: tuple[ProviderModelSpec, ...] = ()
    settings_alias_for: str = ""  # compatibility alias grouped under this provider in Settings

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "openai_codex"
    backend: str = "openai_compat"

    # extra env vars / request headers supplied by the provider integration.
    env_extras: tuple[tuple[str, str], ...] = ()
    default_extra_headers: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    strip_model_prefixes: tuple[str, ...] = ()  # strip only when the first model segment matches
    supports_max_completion_tokens: bool = False

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False

    # Provider is listed for shared credentials but cannot serve chat completions.
    is_transcription_only: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # How to inject the thinking on/off toggle into extra_body.
    # ""              — no extra_body needed (default)
    # "thinking_type" — {"thinking": {"type": "enabled"/"disabled"}}
    #                   (DeepSeek, VolcEngine, BytePlus)
    # "enable_thinking" — {"enable_thinking": true/false}  (DashScope)
    # "reasoning_split" — {"reasoning_split": true/false}  (MiniMax)
    thinking_style: str = ""

    # Gateway-native reasoning control to pair with model-level thinking styles.
    # "reasoning_effort" — {"reasoning": {"effort": <none|minimal|...>}}
    #                      (OpenRouter)
    gateway_reasoning_style: str = ""

    # When True, treat the "reasoning" response field as formal content
    # when "content" is empty.  Only set this for providers (e.g. StepFun)
    # whose API returns the actual answer in "reasoning" instead of "content".
    reasoning_as_content: bool = False

    # Map user-supplied reasoning_effort (OpenAI vocab: minimal/low/medium/high)
    # to the value this provider accepts on the wire. Set when the provider's
    # accepted set differs from OpenAI's. An empty mapped value omits the kwarg.
    # Mistral: only "high"/"none" — low/minimal map to "none", medium maps to "high".
    reasoning_effort_remap: tuple[tuple[str, str], ...] = ()

    # Models whose API rejects the reasoning_effort kwarg because reasoning is
    # implicit (Magistral always reasons; sending the kwarg returns HTTP 400).
    # Substring match against the wire model name (lowercased).
    implicit_reasoning_models: tuple[str, ...] = ()

    # Models that expose the OpenAI Responses wire format.  This is model-level
    # because providers may add Responses support incrementally (DeepSeek V4
    # Flash is supported before V4 Pro).
    responses_models: tuple[str, ...] = ()

    # Provider-hosted Responses tools sent unless extraBody.tools explicitly
    # supplies the hosted-tool selection. Values are raw Responses tool types.
    responses_default_tools: tuple[str, ...] = ()

    # When the model returns content as a list of {"type":"thinking",...} +
    # {"type":"text",...} blocks, extract the thinking text into
    # reasoning_content. Mistral's Magistral / reasoning-enabled responses use
    # this shape.
    extract_thinking_blocks: bool = False

    # Strip ``reasoning_content`` from assistant history messages before
    # sending. Mistral validates its request schema strictly and 400s on
    # any extra fields; other providers (DeepSeek) require this key on the
    # wire to keep thinking-mode history intact.
    strip_history_reasoning_content: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Custom (direct OpenAI-compatible endpoint) ========================
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
    ),
    # === Direct providers (matched by model-name keyword) ==================
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
    ),
    # OpenAI: SDK default base URL (providers.openai.apiBase overrides it)
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
    ),
    # OpenAI Codex: ChatGPT OAuth credential, no API key. Listed after "openai"
    # so a bare "gpt-*" model keeps routing to the API-key provider; Codex is
    # reached through its explicit "openai-codex/" model prefix.
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        backend="openai_codex",
        model_catalog="builtin",
        builtin_models=(
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-sol",
                label="GPT-5.6-Sol",
                description="Frontier agentic coding model.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-terra",
                label="GPT-5.6-Terra",
                description="Balanced agentic coding model for everyday work.",
                context_window=372000,
            ),
            ProviderModelSpec(
                id="openai-codex/gpt-5.6-luna",
                label="GPT-5.6-Luna",
                description="Fast, lower-cost agentic coding model.",
                context_window=372000,
            ),
        ),
        is_oauth=True,
    ),
    # === Auxiliary (not a primary LLM provider) ============================
    # Groq: used for Whisper voice transcription, also usable for LLM chat
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        backend="openai_compat",
        default_api_base="https://api.groq.com/openai/v1",
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "anthropic"."""
    normalized = to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None


def create_dynamic_spec(
    name: str,
    *,
    display_name: str = "",
    thinking_style: str = "",
) -> ProviderSpec:
    """Create a dynamic ProviderSpec for custom user-defined providers."""
    normalized = to_snake(name.replace("-", "_"))
    strip_prefixes = tuple(dict.fromkeys((name, normalized)))
    return ProviderSpec(
        name=normalized,
        keywords=(),
        env_key="",
        display_name=display_name or name.replace("-", " ").replace("_", " ").title(),
        backend="openai_compat",
        is_direct=True,
        strip_model_prefixes=strip_prefixes,
        thinking_style=thinking_style,
    )
