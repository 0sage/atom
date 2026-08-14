# Image Generation

atom can generate and edit images through the `generate_image` tool. Enable the tool in `config.json`, then ask for an image normally in chat; the agent decides when to call it and can keep iterating on generated images in the same conversation.

The feature is disabled by default. Set `tools.imageGeneration.enabled` to `true` with a configured provider and model. The running gateway applies the change immediately.

## Quick Setup

This snippet uses the built-in image-generation default so the JSON has concrete names. Replace `provider` and `model` if you are pointing at your own OpenAI-compatible image endpoint.

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "openai",
      "model": "gpt-image-1"
    }
  }
}
```

See [Provider Notes](#provider-notes) for OpenAI and Custom configuration examples.

> [!TIP]
> Prefer environment variables for API keys. atom resolves `${VAR_NAME}` values from the environment at startup.

## Usage

1. Enable image generation with a configured provider and model.
2. Describe the image or edit you want in chat.
3. Include an aspect ratio or size in the request when the configured defaults are not suitable.
4. Attach reference images when editing an existing image.

Generated images are delivered as assistant media on channels that support it. Follow-up prompts such as "make it warmer", "change the background", or "try a 16:9 version" can reuse the most recent generated artifact: the agent sees the saved artifact path and can pass it back to `generate_image` as `reference_images` for iterative edits.

## Configuration Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tools.imageGeneration.enabled` | boolean | `false` | Register the `generate_image` tool |
| `tools.imageGeneration.provider` | string | `"openai"` | Image provider. Supported values: `openai`, `custom` |
| `tools.imageGeneration.model` | string | `"gpt-image-1"` | Provider model name |
| `tools.imageGeneration.defaultAspectRatio` | string | `"1:1"` | Default ratio when the prompt/tool call does not specify one |
| `tools.imageGeneration.defaultImageSize` | string | `"1K"` | Default size hint, for example `1K`, `2K`, `4K`, or `1024x1024` |
| `tools.imageGeneration.maxImagesPerTurn` | number | `4` | Maximum `count` accepted by one tool call. Valid range: `1` to `8` |
| `tools.imageGeneration.saveDir` | string | `"generated"` | Relative directory under atom's media directory for generated artifacts |

Provider settings reuse normal provider config fields:

| Option | Description |
|--------|-------------|
| `providers.<name>.apiKey` | Provider API key. Prefer `${ENV_VAR}` |
| `providers.<name>.apiBase` | Optional custom base URL |
| `providers.<name>.extraHeaders` | Headers merged into provider requests |
| `providers.<name>.extraBody` | Extra JSON fields merged into provider request bodies |
| `providers.<name>.proxy` | Explicit trusted HTTP proxy for provider requests and returned image URL downloads |

For providers that return image URLs, direct downloads use DNS pinning. When an explicit provider `proxy` is configured, atom rejects malformed URLs and locally identifiable private/internal targets on the initial URL and every redirect. Hostnames unavailable to local DNS are delegated to that trusted proxy, which owns final DNS resolution and network egress. Process-wide proxy environment variables are not used for these downloads.

Both camelCase and snake_case config keys are accepted, but docs use camelCase to match `config.json`.

## Provider Notes

### OpenAI

The `openai` image provider calls the OpenAI Images API with `providers.openai.apiKey`.

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "openai",
      "model": "gpt-image-1"
    }
  }
}
```

Known model names are `gpt-image-2`, `gpt-image-1`, `dall-e-3`, and `dall-e-2`. A leading
`openai/` prefix on the model is stripped before the request.

Size handling depends on the model family, because each accepts a different set:

| Model family | Accepted sizes |
|---|---|
| `gpt-image-*` | `1024x1024`, `1024x1536`, `1536x1024`, plus `auto` |
| `dall-e-3` | `1024x1024`, `1792x1024`, `1024x1792` |
| `dall-e-2` | `256x256`, `512x512`, `1024x1024` |

`defaultAspectRatio` is mapped to the closest size the selected model supports, and an
explicit `defaultImageSize` that the model family does not accept is ignored rather than
sent through and rejected. DALL·E 2 falls back to a square size for non-square ratios.

Reference-image edits are supported on `gpt-image-*` models: atom posts to the images
`edits` endpoint as multipart form data. DALL·E models reject reference images with a clear
error. Set `apiBase` to route image requests through a proxy or gateway that implements the
same API.

### Custom (OpenAI-compatible)

The `custom` image provider fits services that implement the synchronous OpenAI Images API:

```text
POST /v1/images/generations
```

The response must include generated images in `data[].b64_json` or `data[].url`. Native prediction APIs, such as Replicate's `/v1/models/{owner}/{model}/predictions`, are not directly compatible unless you put an OpenAI-compatible gateway in front of them.

Configure:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_IMAGE_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "custom",
      "model": "your-model-name"
    }
  }
}
```

The `apiBase` is required. The provider sends requests to `{apiBase}/images/generations` using the OpenAI Images API format with `response_format: "b64_json"`. The `apiKey` is optional for local or unauthenticated endpoints. Reference-image edits are not supported by the generic `custom` provider.

`extraBody` can adapt provider-specific quirks because it is merged last into the request body. Examples:

- An endpoint that documents URL responses needs `"extraBody": {"response_format": "url"}`.
- An endpoint that documents `"response_format": "base64"` needs that override instead.
- Some hosted Seedream-style models require size hints such as `"2K"`, `"3K"`, or `"4K"`, or explicit dimensions. Set `tools.imageGeneration.defaultImageSize` or `providers.custom.extraBody.size` to a value the selected model supports.

For compatibility with the default atom setting, custom maps `defaultImageSize: "1K"` to `1024x1024`. Other explicit size hints are passed through unchanged.

## Artifacts

Generated images are stored under the active atom instance's media directory:

```text
~/.atom/media/generated/YYYY-MM-DD/img_<id>.<ext>
~/.atom/media/generated/YYYY-MM-DD/img_<id>.json
```

For non-default config locations, the media directory is relative to the active config file's directory.

The JSON sidecar stores:

| Field | Meaning |
|-------|---------|
| `id` | Short generated image id, such as `img_ab12cd34ef56` |
| `path` | Local image path used internally for follow-up edits |
| `mime` | Detected image MIME type |
| `prompt` | Prompt used for the generation |
| `model` | Provider model |
| `provider` | Provider name |
| `source_images` | Reference image paths used for edits |
| `created_at` | Creation timestamp |

Do not paste base64 image payloads into chat. The agent should keep local artifact paths internal unless the user explicitly asks for debugging details.

## Prompting

Good image prompts include:

- Subject and scene.
- Composition, camera, or layout.
- Style, mood, lighting, and color palette.
- Exact text that must appear in the image, quoted.
- Constraints such as "keep the same character" or "preserve the logo".

Example:

```text
A minimal app icon for atom: friendly robot head, rounded square, soft blue and white palette, clean vector style, no text
```

For edits, describe what should change and what must stay fixed:

```text
Use the reference image. Keep the same robot and composition, change the palette to warm orange, and add a subtle sunrise background.
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `generate_image` is not available | Set `tools.imageGeneration.enabled` to `true` with a configured provider and model, then restart the gateway |
| Missing API key error | Configure `providers.<provider>.apiKey`; if using `${VAR_NAME}`, confirm the environment variable is visible to the gateway process |
| `unsupported image generation provider` | Use `openai` or `custom` |
| Generation times out | Try a smaller or default image size, or retry later |
| Reference image rejected | Reference image paths must be inside the workspace or atom media directory and must be valid image files |
