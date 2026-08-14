from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from atom.providers.image_generation import (
    CustomImageGenerationClient,
    GeneratedImageResponse,
    ImageGenerationError,
    OpenAIImageGenerationClient,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xdacd\xfc\xff\x1f\x00\x03\x03"
    b"\x02\x00\xef\xbf\xa7\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 12


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
        content: bytes = b"",
        sse_lines: list[str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.content = content
        self.request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
        self._sse_lines = sse_lines

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request, text=self.text)
            raise httpx.HTTPStatusError("failed", request=self.request, response=response)

    async def aiter_lines(self):
        if self._sse_lines is not None:
            for line in self._sse_lines:
                yield line
            return
        # Fallback: treat response text as SSE lines
        for line in self.text.split("\n"):
            yield line


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.get_response = response
        self.calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return self.get_response


@pytest.fixture(autouse=True)
def generated_image_downloads(monkeypatch) -> list[tuple[str, str | None]]:
    """Keep provider response parsing tests independent from outbound HTTP."""
    downloads: list[tuple[str, str | None]] = []

    async def download(url: str, *, proxy: str | None = None) -> str:
        downloads.append((url, proxy))
        return PNG_DATA_URL

    monkeypatch.setattr(
        "atom.providers.image_generation._download_image_data_url",
        download,
    )
    return downloads


RAW_B64 = PNG_DATA_URL.removeprefix("data:image/png;base64,")


# ---------------------------------------------------------------------------
# StepFun (阶跃星辰)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_payload_and_response() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        api_base="https://api.openai.com/v1",
        extra_headers={"X-Test": "1"},
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(
        prompt="a cat on the moon",
        model="dall-e-3",
        aspect_ratio="16:9",
    )

    assert response.images == [PNG_DATA_URL]
    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/generations"
    assert call["headers"]["Authorization"] == "Bearer sk-openai-test"
    assert call["headers"]["X-Test"] == "1"
    body = call["json"]
    assert body["model"] == "dall-e-3"
    assert body["prompt"] == "a cat on the moon"
    assert body["response_format"] == "b64_json"
    assert body["n"] == 1
    assert body["size"] == "1792x1024"


@pytest.mark.asyncio
async def test_openai_extra_body_null_drops_default_params_only() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        extra_body={
            "response_format": None,
            "seed": 0,
            "safety_checker": False,
        },
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="dall-e-3")

    body = fake.calls[0]["json"]
    assert "response_format" not in body
    assert body["n"] == 1
    assert body["seed"] == 0
    assert body["safety_checker"] is False


@pytest.mark.asyncio
async def test_openai_b64_json_response_uses_detected_mime() -> None:
    raw_b64 = base64.b64encode(JPEG_BYTES).decode("ascii")
    fake = FakeClient(FakeResponse({"data": [{"b64_json": raw_b64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="draw", model="dall-e-3")

    assert response.images == [f"data:image/jpeg;base64,{raw_b64}"]


@pytest.mark.asyncio
async def test_openai_url_download_fallback(
    generated_image_downloads: list[tuple[str, str | None]],
) -> None:
    fake = FakeClient(FakeResponse({"data": [{"url": "https://cdn.example/image.png"}]}))
    fake.get_response = FakeResponse({}, content=PNG_BYTES)
    proxy = "http://127.0.0.1:23458"
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        proxy=proxy,
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="draw", model="dall-e-3")

    assert response.images[0].startswith("data:image/png;base64,")
    assert generated_image_downloads == [("https://cdn.example/image.png", proxy)]


@pytest.mark.asyncio
async def test_openai_multiple_images() -> None:
    fake = FakeClient(FakeResponse({
        "data": [
            {"b64_json": RAW_B64},
            {"b64_json": RAW_B64},
        ]
    }))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="draw", model="dall-e-3")

    assert len(response.images) == 2
    assert response.images == [PNG_DATA_URL, PNG_DATA_URL]


@pytest.mark.asyncio
async def test_openai_aspect_ratio_to_size() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="dall-e-3", aspect_ratio="1:1")
    assert fake.calls[0]["json"]["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_openai_dalle3_uses_supported_orientation_sizes() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="dall-e-3", aspect_ratio="3:4")
    await client.generate(prompt="draw", model="dall-e-3", aspect_ratio="4:3")

    assert fake.calls[0]["json"]["size"] == "1024x1792"
    assert fake.calls[1]["json"]["size"] == "1792x1024"


@pytest.mark.asyncio
async def test_openai_dalle2_uses_square_size_for_non_square_ratios() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="dall-e-2", aspect_ratio="16:9")

    assert fake.calls[0]["json"]["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_openai_gpt_image_uses_supported_landscape_size() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="gpt-image-1", aspect_ratio="16:9")

    assert fake.calls[0]["json"]["size"] == "1536x1024"


@pytest.mark.asyncio
async def test_openai_gpt_image_uses_supported_orientation_sizes() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="gpt-image-1", aspect_ratio="3:4")
    await client.generate(prompt="draw", model="gpt-image-1", aspect_ratio="4:3")

    assert fake.calls[0]["json"]["size"] == "1024x1536"
    assert fake.calls[1]["json"]["size"] == "1536x1024"


@pytest.mark.asyncio
async def test_openai_reference_images_use_edits_endpoint(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG_BYTES)
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        api_base="https://api.openai.com/v1",
        extra_headers={"X-Test": "1"},
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(
        prompt="make a warmer version",
        model="gpt-image-1",
        reference_images=[str(ref)],
        aspect_ratio="16:9",
    )

    assert response.images == [PNG_DATA_URL]
    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/edits"
    assert call["headers"]["Authorization"] == "Bearer sk-openai-test"
    assert call["headers"]["X-Test"] == "1"
    assert "Content-Type" not in call["headers"]
    assert "json" not in call
    assert call["data"]["model"] == "gpt-image-1"
    assert call["data"]["prompt"] == "make a warmer version"
    assert call["data"]["size"] == "1536x1024"
    assert len(call["files"]) == 1
    assert call["files"][0][0] == "image[]"
    assert call["files"][0][1][0] == "ref.png"
    assert call["files"][0][1][2] == "image/png"
    assert call["files"][0][1][1].closed is True


@pytest.mark.asyncio
async def test_openai_reference_images_expand_user_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG_BYTES)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="use a home-relative reference",
        model="gpt-image-1",
        reference_images=["~/ref.png"],
    )

    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/edits"
    assert call["files"][0][0] == "image[]"
    assert call["files"][0][1][0] == "ref.png"
    assert call["files"][0][1][2] == "image/png"
    assert call["files"][0][1][1].closed is True


@pytest.mark.asyncio
async def test_openai_reference_images_send_multiple_multipart_files(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(PNG_BYTES)
    second.write_bytes(PNG_BYTES)
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        extra_body={
            "quality": "high",
            "seed": 0,
            "safety_checker": False,
            "metadata": {"ignored": True},
            "background": None,
        },
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="combine these references",
        model="openai/gpt-image-1",
        reference_images=[str(first), str(second)],
    )

    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/edits"
    assert call["data"]["model"] == "gpt-image-1"
    assert call["data"]["prompt"] == "combine these references"
    assert call["data"]["quality"] == "high"
    assert call["data"]["seed"] == "0"
    assert call["data"]["safety_checker"] == "false"
    assert "metadata" not in call["data"]
    assert "background" not in call["data"]
    assert [item[0] for item in call["files"]] == ["image[]", "image[]"]
    assert [item[1][0] for item in call["files"]] == ["first.png", "second.png"]
    assert all(item[1][1].closed for item in call["files"])


@pytest.mark.asyncio
async def test_openai_gpt_image_without_reference_images_uses_generations_json() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="gpt-image-1", aspect_ratio="16:9")

    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/generations"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"]["model"] == "gpt-image-1"
    assert call["json"]["prompt"] == "draw"
    assert call["json"]["size"] == "1536x1024"
    assert "data" not in call
    assert "files" not in call


@pytest.mark.asyncio
async def test_openai_dalle_reference_images_raise_clear_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG_BYTES)
    client = OpenAIImageGenerationClient(api_key="sk-openai-test")

    with pytest.raises(ImageGenerationError, match="does not support reference images"):
        await client.generate(
            prompt="edit this",
            model="dall-e-3",
            reference_images=[str(ref)],
        )


@pytest.mark.asyncio
async def test_openai_default_size_when_no_aspect_ratio() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(prompt="draw", model="dall-e-3")

    body = fake.calls[0]["json"]
    assert body["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_openai_ignores_explicit_size_unsupported_by_model_family() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="draw",
        model="dall-e-3",
        aspect_ratio="16:9",
        image_size="1536x1024",
    )

    body = fake.calls[0]["json"]
    assert body["size"] == "1792x1024"


@pytest.mark.asyncio
async def test_openai_uses_explicit_image_size() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="draw",
        model="dall-e-3",
        aspect_ratio="16:9",
        image_size="1024x1024",
    )

    body = fake.calls[0]["json"]
    assert body["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_openai_requires_api_key() -> None:
    client = OpenAIImageGenerationClient(api_key=None)

    with pytest.raises(ImageGenerationError, match="API key"):
        await client.generate(prompt="draw", model="dall-e-3")


# ---------------------------------------------------------------------------
# Custom OpenAI-compatible Images API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_generate_success() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = CustomImageGenerationClient(
        api_key="sk-custom-test",
        api_base="https://custom.example/v1/",
        extra_headers={"X-Test": "1"},
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(
        prompt="a cat on the moon",
        model="custom-image-model",
        aspect_ratio="16:9",
    )

    assert isinstance(response, GeneratedImageResponse)
    assert response.images == [PNG_DATA_URL]
    assert response.content == ""
    call = fake.calls[0]
    assert call["url"] == "https://custom.example/v1/images/generations"
    assert call["headers"]["Authorization"] == "Bearer sk-custom-test"
    assert call["headers"]["X-Test"] == "1"
    body = call["json"]
    assert body["model"] == "custom-image-model"
    assert body["prompt"] == "a cat on the moon"
    assert body["response_format"] == "b64_json"
    assert body["n"] == 1
    assert body["size"] == "1536x1024"


@pytest.mark.asyncio
async def test_custom_generate_preserves_provider_size_hint() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = CustomImageGenerationClient(
        api_key="sk-custom-test",
        api_base="https://custom.example/v1",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="a cat on the moon",
        model="custom-image-model",
        image_size="2K",
    )

    assert fake.calls[0]["json"]["size"] == "2K"


@pytest.mark.asyncio
async def test_custom_generate_maps_one_k_to_openai_dimension() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = CustomImageGenerationClient(
        api_key="sk-custom-test",
        api_base="https://custom.example/v1",
        client=fake,  # type: ignore[arg-type]
    )

    await client.generate(
        prompt="a cat on the moon",
        model="custom-image-model",
        image_size="1K",
    )

    assert fake.calls[0]["json"]["size"] == "1024x1024"


@pytest.mark.asyncio
async def test_custom_generate_extra_body_can_override_defaults(
    generated_image_downloads: list[tuple[str, str | None]],
) -> None:
    fake = FakeClient(FakeResponse({"data": [{"url": "https://images.example/cat.png"}]}))
    fake.get_response = FakeResponse({}, content=PNG_BYTES)
    proxy = "http://127.0.0.1:23458"
    client = CustomImageGenerationClient(
        api_key="sk-custom-test",
        api_base="https://custom.example/v1",
        extra_body={"response_format": "url", "size": "2K"},
        proxy=proxy,
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(
        prompt="a cat on the moon",
        model="custom-image-model",
        image_size="1K",
    )

    assert response.images == [PNG_DATA_URL]
    assert generated_image_downloads == [("https://images.example/cat.png", proxy)]
    body = fake.calls[0]["json"]
    assert body["response_format"] == "url"
    assert body["size"] == "2K"


@pytest.mark.asyncio
async def test_custom_generate_without_api_key_omits_authorization() -> None:
    fake = FakeClient(FakeResponse({"data": [{"b64_json": RAW_B64}]}))
    client = CustomImageGenerationClient(
        api_key=None,
        api_base="http://localhost:7860/v1",
        client=fake,  # type: ignore[arg-type]
    )

    response = await client.generate(prompt="draw", model="custom-image-model")

    assert response.images == [PNG_DATA_URL]
    assert "Authorization" not in fake.calls[0]["headers"]


@pytest.mark.asyncio
async def test_custom_generate_requires_api_base() -> None:
    client = CustomImageGenerationClient(api_key="sk-custom-test")

    with pytest.raises(ImageGenerationError, match="providers.custom.apiBase"):
        await client.generate(prompt="draw", model="custom-image-model")


@pytest.mark.asyncio
async def test_custom_generate_http_error() -> None:
    fake = FakeClient(FakeResponse({"error": "bad request"}, status_code=400))
    client = CustomImageGenerationClient(
        api_key="sk-custom-test",
        api_base="https://custom.example/v1",
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(ImageGenerationError, match="HTTP 400"):
        await client.generate(prompt="draw", model="custom-image-model")


# ---------------------------------------------------------------------------
# OpenAI Codex (Responses API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_no_images_raises() -> None:
    fake = FakeClient(FakeResponse({"data": []}))
    client = OpenAIImageGenerationClient(
        api_key="sk-openai-test",
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(ImageGenerationError, match="returned no images"):
        await client.generate(prompt="draw", model="dall-e-3")


# ---------------------------------------------------------------------------
# Zhipu
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ModelScope (魔搭) image generation tests
# ---------------------------------------------------------------------------


def test_image_provider_http_client_kwargs_include_explicit_proxy() -> None:
    proxy = "http://127.0.0.1:23458"
    client = OpenAIImageGenerationClient(
        api_key="sk-test",
        proxy=proxy,
    )

    assert client._http_client_kwargs() == {
        "timeout": client.timeout,
        "proxy": proxy,
        "trust_env": False,
    }
