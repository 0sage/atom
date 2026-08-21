"""Routing and construction rules for the OAuth-based OpenAI Codex provider.

Codex holds no API key, so it must resolve on an explicit model prefix alone —
and it must never win a keyword match or a blind fallback that belongs to the
API-key OpenAI provider.
"""

import pytest

from atom.config.schema import Config, ProviderConfig
from atom.providers.factory import make_provider, validate_provider_setup
from atom.providers.openai_codex_provider import OpenAICodexProvider
from atom.providers.registry import PROVIDERS, find_by_name

_CODEX_MODEL = "openai-codex/gpt-5.6-sol"


def _config(**providers: ProviderConfig) -> Config:
    config = Config()
    for name, value in providers.items():
        setattr(config.providers, name, value)
    return config


def _preset(config: Config, model: str):
    config.agents.defaults.model = model
    return config.resolve_preset()


# ======================================================================
# Registry entry
# ======================================================================


class TestRegistryEntry:
    def test_spec_exists_and_is_oauth(self):
        spec = find_by_name("openai_codex")
        assert spec is not None
        assert spec.is_oauth is True
        assert spec.backend == "openai_codex"
        assert spec.env_key == ""

    def test_hyphenated_name_resolves(self):
        assert find_by_name("openai-codex") is find_by_name("openai_codex")

    def test_listed_after_openai_so_gpt_keywords_keep_their_owner(self):
        names = [spec.name for spec in PROVIDERS]
        assert names.index("openai") < names.index("openai_codex")

    def test_keyword_cannot_collide_with_bare_gpt_models(self):
        spec = find_by_name("openai_codex")
        assert spec is not None
        assert spec.keywords == ("openai-codex",)

    def test_builtin_models_are_prefixed_and_carry_windows(self):
        spec = find_by_name("openai_codex")
        assert spec is not None
        assert spec.model_catalog == "builtin"
        assert spec.builtin_models
        for model in spec.builtin_models:
            assert model.id.startswith("openai-codex/")
            assert model.context_window and model.context_window > 0

    def test_config_exposes_a_matching_provider_field(self):
        """schema lookups use getattr(providers, spec.name); the field must exist."""
        spec = find_by_name("openai_codex")
        assert spec is not None
        assert isinstance(getattr(Config().providers, spec.name), ProviderConfig)


# ======================================================================
# Model routing
# ======================================================================


class TestModelRouting:
    def test_prefix_resolves_without_an_api_key(self):
        config = _config()
        preset = _preset(config, _CODEX_MODEL)
        assert config.get_provider_name(_CODEX_MODEL, preset=preset) == "openai_codex"

    def test_underscore_prefix_also_resolves(self):
        model = "openai_codex/gpt-5.6-sol"
        config = _config()
        preset = _preset(config, model)
        assert config.get_provider_name(model, preset=preset) == "openai_codex"

    def test_bare_gpt_model_still_routes_to_the_api_key_provider(self):
        config = _config(openai=ProviderConfig(api_key="sk-test"))
        preset = _preset(config, "gpt-4o")
        assert config.get_provider_name("gpt-4o", preset=preset) == "openai"

    def test_explicit_openai_prefix_is_unaffected(self):
        config = _config(openai=ProviderConfig(api_key="sk-test"))
        preset = _preset(config, "openai/gpt-4o")
        assert config.get_provider_name("openai/gpt-4o", preset=preset) == "openai"

    def test_codex_is_not_used_as_a_blind_fallback(self):
        """An unqualified model must not silently land on the OAuth provider."""
        config = _config(anthropic=ProviderConfig(api_key="sk-ant"))
        preset = _preset(config, "some-unknown-model")
        assert config.get_provider_name("some-unknown-model", preset=preset) != "openai_codex"

    def test_codex_wins_its_own_prefix_even_when_openai_is_configured(self):
        config = _config(openai=ProviderConfig(api_key="sk-test"))
        preset = _preset(config, _CODEX_MODEL)
        assert config.get_provider_name(_CODEX_MODEL, preset=preset) == "openai_codex"


# ======================================================================
# Construction
# ======================================================================


class TestProviderConstruction:
    def test_validates_with_no_api_key(self):
        config = _config()
        validate_provider_setup(config, preset=_preset(config, _CODEX_MODEL))

    def test_factory_builds_the_codex_backend(self):
        config = _config()
        provider = make_provider(config, preset=_preset(config, _CODEX_MODEL))
        assert isinstance(provider, OpenAICodexProvider)
        assert provider.get_default_model() == _CODEX_MODEL
        assert provider.api_key is None

    def test_generation_settings_are_applied(self):
        config = _config()
        _preset(config, _CODEX_MODEL)
        config.agents.defaults.reasoning_effort = "high"
        provider = make_provider(config, preset=config.resolve_preset())
        assert provider.generation.reasoning_effort == "high"

    def test_proxy_is_accepted(self):
        """The kit and the provider both take a proxy, unlike the anthropic backend."""
        config = _config(openai_codex=ProviderConfig(proxy="http://proxy.local:8080"))
        provider = make_provider(config, preset=_preset(config, _CODEX_MODEL))
        assert isinstance(provider, OpenAICodexProvider)
        assert provider.proxy == "http://proxy.local:8080"

    def test_proxy_is_still_rejected_for_anthropic(self):
        config = _config(anthropic=ProviderConfig(api_key="sk-ant", proxy="http://p:1"))
        with pytest.raises(ValueError, match="proxy is only supported"):
            validate_provider_setup(config, preset=_preset(config, "claude-sonnet-5"))

    def test_extra_body_and_headers_reach_the_provider(self):
        config = _config(
            openai_codex=ProviderConfig(
                extra_body={"text": {"verbosity": "low"}},
                extra_headers={"x-trace": "abc"},
            )
        )
        provider = make_provider(config, preset=_preset(config, _CODEX_MODEL))
        assert isinstance(provider, OpenAICodexProvider)
        captured = provider._build_headers("acct", "tok")  # pyright: ignore[reportPrivateUsage]
        assert captured["x-trace"] == "abc"


# ======================================================================
# Onboarding visibility
# ======================================================================


class TestOnboardingVisibility:
    def test_hidden_from_api_key_onboarding(self):
        from atom.cli.onboard import _get_provider_info  # pyright: ignore[reportPrivateUsage]

        assert "openai_codex" not in _get_provider_info()

    def test_hidden_from_quick_start(self):
        from atom.cli.onboard import (  # pyright: ignore[reportPrivateUsage]
            _get_quick_start_provider_info,
        )

        assert "openai_codex" not in _get_quick_start_provider_info()


# ======================================================================
# CLI surface
# ======================================================================


class TestAuthCommandAliases:
    @pytest.mark.parametrize("given", ["openai-codex", "openai_codex", "codex", "OpenAI-Codex"])
    def test_accepted_aliases(self, given: str):
        from atom.cli.commands import _resolve_oauth_provider  # pyright: ignore[reportPrivateUsage]

        assert _resolve_oauth_provider(given) == "openai_codex"

    def test_unknown_provider_exits(self):
        import typer

        from atom.cli.commands import _resolve_oauth_provider  # pyright: ignore[reportPrivateUsage]

        with pytest.raises(typer.Exit):
            _resolve_oauth_provider("anthropic")
