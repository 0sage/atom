"""Tests for lazy provider exports from atom.providers."""

from __future__ import annotations

import importlib
import sys


def test_importing_providers_package_is_lazy(monkeypatch) -> None:
    original_package = sys.modules["atom.providers"]
    monkeypatch.delitem(sys.modules, "atom.providers", raising=False)
    monkeypatch.delitem(sys.modules, "atom.providers.anthropic_provider", raising=False)
    monkeypatch.delitem(sys.modules, "atom.providers.openai_compat_provider", raising=False)

    try:
        providers = importlib.import_module("atom.providers")

        assert "atom.providers.anthropic_provider" not in sys.modules
        assert "atom.providers.openai_compat_provider" not in sys.modules
        assert providers.__all__ == [
            "LLMProvider",
            "LLMResponse",
            "AnthropicProvider",
            "OpenAICompatProvider",
        ]
    finally:
        # Importing a replacement subpackage also replaces atom.providers on the
        # parent package. Restore both views so this isolation test cannot pollute
        # later tests that resolve a module through a dotted monkeypatch target.
        monkeypatch.undo()
        setattr(sys.modules["atom"], "providers", original_package)


def test_explicit_provider_import_still_works(monkeypatch) -> None:
    original_package = sys.modules["atom.providers"]
    monkeypatch.delitem(sys.modules, "atom.providers", raising=False)
    monkeypatch.delitem(sys.modules, "atom.providers.anthropic_provider", raising=False)

    try:
        namespace: dict[str, object] = {}
        exec("from atom.providers import AnthropicProvider", namespace)

        assert namespace["AnthropicProvider"].__name__ == "AnthropicProvider"
        assert "atom.providers.anthropic_provider" in sys.modules
    finally:
        monkeypatch.undo()
        setattr(sys.modules["atom"], "providers", original_package)


def test_openai_compat_provider_is_importable_on_demand() -> None:
    from atom.providers.openai_compat_provider import OpenAICompatProvider

    assert OpenAICompatProvider.__name__ == "OpenAICompatProvider"
