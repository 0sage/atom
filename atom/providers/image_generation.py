"""Image generation provider helpers."""

from __future__ import annotations

import base64
import binascii
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin

import httpx
from loguru import logger

from atom.config.schema import Config, ProviderConfig
from atom.providers.registry import find_by_name
from atom.security.network import (
    PinnedDNSAsyncTransport,
    UnsafeURLRequestError,
    resolve_url_target,
)
from atom.utils.helpers import detect_image_mime

_DEFAULT_TIMEOUT_S = 120.0
_IMAGE_DOWNLOAD_MAX_BYTES = 32 * 1024 * 1024
_IMAGE_DOWNLOAD_MAX_REDIRECTS = 5
# Aspect ratios documented for every Gemini image model using generateContent.
# Gemini 3.1 Flash and Flash Lite additionally accept extreme aspect ratios.
# Gemini 3 Pro image models accept these sizes. Gemini 3.1 Flash adds 512,
# while Gemini 3.1 Flash Lite supports only 1K.


class ImageGenerationError(RuntimeError):
    """Raised when the image generation provider cannot return images."""


@dataclass(frozen=True)
class GeneratedImageResponse:
    """Images and optional text returned by the provider."""

    images: list[str]
    content: str
    raw: dict[str, Any]


def _as_json_objects(value: object) -> list[dict[str, Any]]:
    """Return object entries from an untrusted provider response array."""
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[object], value) if isinstance(item, dict)]


def _read_image_b64(path: str | Path) -> tuple[str, str]:
    """Return ``(mime, base64)`` for the image at ``path``."""
    p = Path(path).expanduser()
    raw = p.read_bytes()
    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError(f"unsupported reference image: {p}")
    return mime, base64.b64encode(raw).decode("ascii")


def image_path_to_data_url(path: str | Path) -> str:
    """Convert a local image path to an image data URL."""
    mime, encoded = _read_image_b64(path)
    return f"data:{mime};base64,{encoded}"


def image_path_to_inline_data(path: str | Path) -> dict[str, str]:
    """Convert a local image path to a Gemini ``inlineData`` payload dict."""
    mime, encoded = _read_image_b64(path)
    return {"mimeType": mime, "data": encoded}


def _b64_image_data_url(value: str) -> str:
    encoded = "".join(value.split())
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ImageGenerationError("generated image payload was not valid base64") from exc
    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError("generated image payload was not a supported image")
    return f"data:{mime};base64,{encoded}"


async def _download_image_data_url(
    url: str,
    *,
    proxy: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    try:
        client_kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "timeout": _DEFAULT_TIMEOUT_S,
            "trust_env": False,
        }
        if proxy:
            # An explicit provider proxy is a user-selected trusted egress boundary.
            # Validate each URL locally, while the proxy owns final DNS resolution.
            client_kwargs["proxy"] = proxy
        else:
            client_kwargs["transport"] = PinnedDNSAsyncTransport(inner=transport)

        async with httpx.AsyncClient(**client_kwargs) as client:
            current_url = url
            for _ in range(_IMAGE_DOWNLOAD_MAX_REDIRECTS + 1):
                if proxy:
                    ok, error, _ = resolve_url_target(
                        current_url,
                        trust_remote_dns=True,
                    )
                    if not ok:
                        raise ImageGenerationError(
                            f"blocked unsafe generated image URL: {error}"
                        )
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ImageGenerationError(
                                "generated image URL redirected without a location"
                            )
                        current_url = urljoin(str(response.url), location)
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise ImageGenerationError(
                            f"failed to download generated image (HTTP {response.status_code})"
                        ) from exc

                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            if int(declared_size) > _IMAGE_DOWNLOAD_MAX_BYTES:
                                raise ImageGenerationError(
                                    "generated image exceeded the 32 MiB download limit"
                                )
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _IMAGE_DOWNLOAD_MAX_BYTES:
                            raise ImageGenerationError(
                                "generated image exceeded the 32 MiB download limit"
                            )
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    break
            else:
                raise ImageGenerationError("generated image URL exceeded the redirect limit")
    except UnsafeURLRequestError as exc:
        raise ImageGenerationError(f"blocked unsafe generated image URL: {exc}") from exc
    except httpx.RequestError as exc:
        raise ImageGenerationError(f"failed to download generated image: {exc}") from exc

    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError("generated image URL did not return a supported image")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_IMAGE_GEN_PROVIDERS: dict[str, type[ImageGenerationProvider]] = {}


def register_image_gen_provider(cls: type[ImageGenerationProvider]) -> None:
    """Register an image provider at import time only.

    The registry is populated by module side effects so provider discovery
    stays lazy and consistent across the process.
    """
    name = cls.provider_name
    if not name:
        raise ValueError(f"{cls.__name__} must set provider_name")
    _IMAGE_GEN_PROVIDERS[name] = cls


def get_image_gen_provider(name: str) -> type[ImageGenerationProvider] | None:
    return _IMAGE_GEN_PROVIDERS.get(name)


def image_gen_provider_names() -> tuple[str, ...]:
    """Return registered image generation provider names in registry order."""
    return tuple(_IMAGE_GEN_PROVIDERS)


def image_gen_provider_configs(config: Config) -> dict[str, ProviderConfig]:
    providers_cfg = config.providers
    return {
        name: pc
        for name in _IMAGE_GEN_PROVIDERS
        if (pc := getattr(providers_cfg, name, None)) is not None
    }


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ImageGenerationProvider(ABC):
    """Base class for image generation provider clients."""

    provider_name: str = ""
    model_options: tuple[str, ...] = ()
    missing_key_message: str = ""
    default_timeout: float = _DEFAULT_TIMEOUT_S

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = self._resolve_base_url(api_base)
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.proxy = proxy or None
        self.timeout = timeout if timeout is not None else self.default_timeout
        self._client = client

    def _resolve_base_url(self, api_base: str | None) -> str:
        if api_base:
            return api_base.rstrip("/")
        spec = find_by_name(self.provider_name)
        if spec and spec.default_api_base:
            return spec.default_api_base.rstrip("/")
        return self._default_base_url()

    def _default_base_url(self) -> str:
        return ""

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImageResponse: ...

    def _require_images(self, images: list[str], data: dict[str, Any]) -> None:
        if images:
            return
        provider_error = data.get("error")
        label = self.provider_name
        if provider_error:
            raise ImageGenerationError(f"{label} returned no images: {provider_error}")
        raise ImageGenerationError(f"{label} returned no images for this request")

    def _http_client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
            kwargs["trust_env"] = False
        return kwargs

    async def _http_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        client: httpx.AsyncClient | None = None,
    ) -> httpx.Response:
        if client is not None:
            return await client.post(url, headers=headers, json=body)
        if self._client is not None:
            return await self._client.post(url, headers=headers, json=body)
        async with httpx.AsyncClient(**self._http_client_kwargs()) as c:
            return await c.post(url, headers=headers, json=body)


# ---------------------------------------------------------------------------
# OpenAI image generation
# ---------------------------------------------------------------------------

_OPENAI_DALLE2_SUPPORTED_SIZES = {"256x256", "512x512", "1024x1024"}
_OPENAI_DALLE3_SUPPORTED_SIZES = {"1024x1024", "1792x1024", "1024x1792"}
_OPENAI_GPT_IMAGE_SUPPORTED_SIZES = {
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "auto",
}
_OPENAI_DALLE2_ASPECT_RATIO_SIZES = {
    "1:1": "1024x1024",
    "16:9": "1024x1024",
    "9:16": "1024x1024",
    "3:4": "1024x1024",
    "4:3": "1024x1024",
}
_OPENAI_DALLE3_ASPECT_RATIO_SIZES = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "3:4": "1024x1792",
    "4:3": "1792x1024",
}
_OPENAI_GPT_IMAGE_ASPECT_RATIO_SIZES = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "3:4": "1024x1536",
    "4:3": "1536x1024",
}


class OpenAIImageGenerationClient(ImageGenerationProvider):
    """OpenAI Images API using an API key (``providers.openai.apiKey``)."""

    provider_name = "openai"
    model_options = ("gpt-image-2", "gpt-image-1", "dall-e-3", "dall-e-2")
    missing_key_message = (
        "OpenAI API key is not configured. Set providers.openai.apiKey."
    )

    def _default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    @staticmethod
    def _strip_model_prefix(model: str) -> str:
        """Remove a leading ``openai/`` prefix if present."""
        if model.startswith("openai/"):
            return model.split("/", 1)[1]
        return model

    async def _parse_images_response(self, payload: dict[str, Any]) -> list[str]:
        return await _openai_images_from_payload(payload, proxy=self.proxy)

    async def _post_image_edit(
        self,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        reference_images: list[str],
    ) -> httpx.Response:
        files: list[tuple[str, tuple[str, Any, str]]] = []
        handles: list[Any] = []
        try:
            for path in reference_images:
                p = Path(path).expanduser()
                raw = p.read_bytes()
                mime = detect_image_mime(raw)
                if mime is None:
                    raise ImageGenerationError(f"unsupported reference image: {p}")
                handle = p.open("rb")
                handles.append(handle)
                files.append(("image[]", (p.name, handle, mime)))

            client = self._client
            if client is not None:
                return await client.post(
                    f"{self.api_base}/images/edits",
                    headers=headers,
                    data=body,
                    files=files,
                )
            async with httpx.AsyncClient(**self._http_client_kwargs()) as c:
                return await c.post(
                    f"{self.api_base}/images/edits",
                    headers=headers,
                    data=body,
                    files=files,
                )
        finally:
            for handle in handles:
                handle.close()

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImageResponse:
        if not self.api_key:
            raise ImageGenerationError(self.missing_key_message)

        clean_model = self._strip_model_prefix(model)

        generation_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        edit_headers = {
            "Authorization": f"Bearer {self.api_key}",
            **self.extra_headers,
        }

        body: dict[str, Any] = {
            "model": clean_model,
            "prompt": prompt,
        }

        if not _openai_is_gpt_image_model(clean_model):
            body["response_format"] = "b64_json"
            body["n"] = 1

        size = _openai_size(clean_model, aspect_ratio, image_size)
        if size:
            body["size"] = size

        body.update(self.extra_body)
        # Drop null-valued params so extraBody can opt out of defaults like response_format.
        body = {key: value for key, value in body.items() if value is not None}

        refs = list(reference_images or [])
        if refs:
            if not _openai_is_gpt_image_model(clean_model):
                raise ImageGenerationError(
                    f"OpenAI model '{clean_model}' does not support reference images; "
                    "use a GPT Image model"
                )
            edit_body = _openai_multipart_form_body(body)
            logger.info(
                "OpenAI Images API request: POST {}/images/edits body={} reference_images={}",
                self.api_base,
                edit_body,
                len(refs),
            )
            response = await self._post_image_edit(
                headers=edit_headers,
                body=edit_body,
                reference_images=refs,
            )
        else:
            logger.info(
                "OpenAI Images API request: POST {}/images/generations body={}",
                self.api_base,
                body,
            )

            response = await self._http_post(
                f"{self.api_base}/images/generations",
                headers=generation_headers,
                body=body,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:1000]
            logger.error("OpenAI Images API error ({}): {}", response.status_code, detail)
            raise ImageGenerationError(
                f"OpenAI image generation failed (HTTP {response.status_code}): {detail}"
            ) from exc

        payload = response.json()
        logger.info("OpenAI Images API response ({}): {}", response.status_code,
                       {k: v for k, v in payload.items() if k != "data"})

        images = await self._parse_images_response(payload)
        self._require_images(images, payload)

        return GeneratedImageResponse(images=images, content="", raw=payload)


class CustomImageGenerationClient(ImageGenerationProvider):
    """OpenAI-compatible Images API for user-configured custom providers."""

    provider_name = "custom"
    missing_base_message = (
        "Custom image generation API base is not configured. Set providers.custom.apiBase."
    )

    def _default_base_url(self) -> str:
        return ""

    @staticmethod
    def _custom_size(aspect_ratio: str | None, image_size: str | None) -> str:
        if image_size:
            requested = image_size.strip()
            if requested:
                if requested.lower() == "1k":
                    return "1024x1024"
                return requested
        return _openai_size("gpt-image-2", aspect_ratio, None)

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImageResponse:
        if not self.api_base:
            raise ImageGenerationError(self.missing_base_message)

        if reference_images:
            logger.warning(
                "Custom image generation does not support reference images; "
                "ignoring {} reference image(s) for {}",
                len(reference_images),
                model,
            )

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "b64_json",
            "n": 1,
            "size": self._custom_size(aspect_ratio, image_size),
        }
        body.update(self.extra_body)

        logger.info("Custom Images API request: POST {}/images/generations body={}", self.api_base, body)

        response = await self._http_post(
            f"{self.api_base}/images/generations",
            headers=headers,
            body=body,
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:1000]
            logger.error("Custom Images API error ({}): {}", response.status_code, detail)
            raise ImageGenerationError(
                f"Custom image generation failed (HTTP {response.status_code}): {detail}"
            ) from exc

        payload = response.json()
        logger.info("Custom Images API response ({}): {}", response.status_code,
                       {k: v for k, v in payload.items() if k != "data"})

        images = await _openai_images_from_payload(payload, proxy=self.proxy)

        self._require_images(images, payload)

        return GeneratedImageResponse(images=images, content="", raw=payload)


# ---------------------------------------------------------------------------
# OpenAI Codex image generation
# ---------------------------------------------------------------------------


def _openai_size(
    model: str,
    aspect_ratio: str | None,
    image_size: str | None,
) -> str:
    """Resolve aspect ratio or image_size to an OpenAI Images API size string."""
    sizes, supported_sizes = _openai_size_options(model)
    explicit_size = _normalize_openai_image_size(image_size)
    if explicit_size and _openai_explicit_size_supported(
        explicit_size,
        supported_sizes=supported_sizes,
    ):
        return explicit_size
    if explicit_size:
        logger.warning(
            "OpenAI image size '{}' is not supported by {}; using aspect ratio/default size",
            explicit_size,
            model,
        )
    if aspect_ratio and aspect_ratio in sizes:
        return sizes[aspect_ratio]
    return "1024x1024"


def _openai_multipart_form_body(body: dict[str, Any]) -> dict[str, str]:
    form: dict[str, str] = {}
    for key, value in body.items():
        if value is None:
            continue
        if isinstance(value, bool):
            form[key] = "true" if value else "false"
        elif isinstance(value, str | int | float):
            form[key] = str(value)
        else:
            logger.warning(
                "OpenAI image edit parameter '{}' is not a scalar form field; ignoring it",
                key,
            )
    return form


def _openai_is_gpt_image_model(model: str) -> bool:
    normalized = model.lower()
    return normalized.startswith(("gpt-image", "chatgpt-image"))


def _openai_size_options(model: str) -> tuple[dict[str, str], set[str] | None]:
    normalized = model.lower()
    if normalized.startswith("dall-e-2"):
        return _OPENAI_DALLE2_ASPECT_RATIO_SIZES, _OPENAI_DALLE2_SUPPORTED_SIZES
    if normalized.startswith("dall-e-3"):
        return _OPENAI_DALLE3_ASPECT_RATIO_SIZES, _OPENAI_DALLE3_SUPPORTED_SIZES
    if normalized.startswith("gpt-image-2"):
        return _OPENAI_GPT_IMAGE_ASPECT_RATIO_SIZES, None
    return _OPENAI_GPT_IMAGE_ASPECT_RATIO_SIZES, _OPENAI_GPT_IMAGE_SUPPORTED_SIZES


def _normalize_openai_image_size(image_size: str | None) -> str | None:
    if not image_size:
        return None
    normalized = image_size.strip().lower()
    return normalized or None


def _openai_explicit_size_supported(
    size: str,
    *,
    supported_sizes: set[str] | None,
) -> bool:
    if supported_sizes is not None:
        return size in supported_sizes
    width, sep, height = size.partition("x")
    return bool(sep and width.isdecimal() and height.isdecimal())


async def _openai_images_from_payload(
    payload: dict[str, Any],
    *,
    proxy: str | None = None,
) -> list[str]:
    """Extract images from OpenAI Images API response.

    Handles both ``b64_json`` (preferred) and ``url`` (downloaded) formats.
    """
    images: list[str] = []
    for item in _as_json_objects(payload.get("data")):
        b64 = item.get("b64_json")
        if isinstance(b64, str) and b64:
            images.append(_b64_image_data_url(b64))
            continue
        url = item.get("url")
        if isinstance(url, str) and url:
            images.append(await _download_image_data_url(url, proxy=proxy))
    return images


# ---------------------------------------------------------------------------
# StepFun (阶跃星辰) image generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Zhipu (智谱) image generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ModelScope (魔搭) image generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

register_image_gen_provider(CustomImageGenerationClient)
register_image_gen_provider(OpenAIImageGenerationClient)
