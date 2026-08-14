"""Application-level audio transcription service.

This module owns atom's transcription behavior: config resolution,
legacy channel fallback, and dispatch to provider adapters. It deliberately
does not know provider-specific HTTP details; those live in
``atom.providers.transcription``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from atom.audio.transcription_registry import (
    get_transcription_provider,
    resolve_transcription_provider,
)
from atom.config.loader import resolve_env_refs
from atom.config.schema import Config, ProviderConfig
from atom.providers.registry import find_by_name

TranscriptionProviderName = str

_DEFAULT_PROVIDER: TranscriptionProviderName = "groq"


@dataclass(frozen=True)
class EffectiveTranscriptionConfig:
    enabled: bool
    provider: TranscriptionProviderName
    model: str
    language: str | None
    api_key: str = field(repr=False)
    api_base: str
    max_duration_sec: int
    max_upload_mb: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _as_provider(value: Any) -> TranscriptionProviderName | None:
    spec = resolve_transcription_provider(value)
    return spec.name if spec else None


def _provider_config(config: Config, provider: str) -> ProviderConfig | None:
    value = getattr(config.providers, provider, None)
    return value if isinstance(value, ProviderConfig) else None


def _provider_default_api_base(provider: str) -> str | None:
    spec = find_by_name(provider)
    return spec.default_api_base if spec else None


def _resolve_transcription_api_key(
    provider: str,
    provider_cfg: ProviderConfig | None,
) -> str:
    api_key = resolve_env_refs(getattr(provider_cfg, "api_key", None) or "") if provider_cfg else ""
    if api_key:
        return api_key

    spec = find_by_name(provider)
    env_key = spec.env_key if spec else ""
    return os.environ.get(env_key, "") if env_key else ""


def _resolve_transcription_api_base(
    provider: str,
    provider_cfg: ProviderConfig | None,
) -> str:
    api_base = resolve_env_refs(getattr(provider_cfg, "api_base", None) or "") if provider_cfg else ""
    if api_base:
        return api_base
    return _provider_default_api_base(provider) or ""


def resolve_transcription_config(config: Config) -> EffectiveTranscriptionConfig:
    """Resolve top-level transcription settings with legacy channel fallback."""
    top = getattr(config, "transcription", None)
    channels = getattr(config, "channels", None)
    provider = (
        _as_provider(getattr(top, "provider", None))
        or _as_provider(getattr(channels, "transcription_provider", None))
        or _DEFAULT_PROVIDER
    )
    spec = get_transcription_provider(provider)
    if spec is None:
        logger.warning("Unknown transcription provider {}; falling back to {}", provider, _DEFAULT_PROVIDER)
        provider = _DEFAULT_PROVIDER
        spec = get_transcription_provider(provider)
    default_model = spec.default_model if spec else ""
    provider_cfg = _provider_config(config, provider)
    return EffectiveTranscriptionConfig(
        enabled=bool(getattr(top, "enabled", True)),
        provider=provider,
        model=(getattr(top, "model", None) or default_model).strip(),
        language=getattr(top, "language", None) or getattr(channels, "transcription_language", None),
        api_key=_resolve_transcription_api_key(provider, provider_cfg),
        api_base=_resolve_transcription_api_base(provider, provider_cfg),
        max_duration_sec=int(getattr(top, "max_duration_sec", 120)),
        max_upload_mb=int(getattr(top, "max_upload_mb", 25)),
    )


async def transcribe_audio_file(
    file_path: str | Path,
    config: EffectiveTranscriptionConfig,
) -> str:
    """Transcribe *file_path* using the already-resolved transcription config."""
    if not config.enabled or not config.configured:
        return ""
    spec = get_transcription_provider(config.provider)
    if spec is None:
        logger.warning("Unknown transcription provider: {}", config.provider)
        return ""
    provider = spec.load_adapter()(
        api_key=config.api_key,
        api_base=config.api_base or None,
        language=config.language,
        model=config.model,
    )
    return await provider.transcribe(file_path)
