"""OpenAI Codex provider — the Responses API over a ChatGPT OAuth credential.

Codex speaks the OpenAI Responses protocol, so the wire handling reuses
``atom.providers.openai_responses`` wholesale. What is Codex-specific lives
here: the OAuth headers, the ``store=false`` item contract, and native
compaction driven by an inline ``compaction_trigger`` input item instead of the
``context_management`` request field the public endpoint accepts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from loguru import logger

from atom.providers.base import (
    LLMProvider,
    LLMResponse,
    ProviderCallContext,
    ProviderConversationState,
    resolve_stream_idle_timeout_s,
)
from atom.providers.openai_codex_auth import get_codex_token
from atom.providers.openai_responses import (
    ResponsesStreamCapture,
    build_responses_state,
    consume_sse_with_reasoning,
    convert_tools,
    is_compaction_compatibility_error,
    is_replayable_finish_reason,
    prepare_responses_input,
    resolve_compact_threshold,
    responses_state_context_tokens,
    responses_state_items,
    responses_state_matches,
)

DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "openai-codex/gpt-5.6-sol"

# Sent as the ``originator`` header and at login. The backend associates the
# credential with the client that requested it, so both must agree.
CODEX_ORIGINATOR = "atom"

_MODEL_PREFIXES = ("openai-codex/", "openai_codex/")
_COMPACTION_RETAINED_CHAR_BUDGET = 256_000
_COMPACTION_ITEM_TYPES = frozenset({"compaction", "compaction_summary", "context_compaction"})
_COMPACTION_MARKERS = ("context_management", "compact_threshold", "compaction_trigger")
_LOGIN_HINT = "Run `atom auth login openai-codex` to sign in with your ChatGPT account."


class OpenAICodexProvider(LLMProvider):
    """Call the Codex Responses endpoint using a ChatGPT OAuth credential."""

    supports_progress_deltas = True

    def __init__(
        self,
        default_model: str = DEFAULT_MODEL,
        proxy: str | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key=None, api_base=None)
        self.default_model = default_model
        self.proxy = proxy or None
        self._extra_body = dict(extra_body or {})
        self._extra_headers = dict(extra_headers or {})
        self._native_compaction_available = True

    # -- public provider surface ------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        provider_context: ProviderCallContext | None = None,
    ) -> LLMResponse:
        _ = temperature
        return await self._call_codex(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            provider_context=provider_context,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        provider_context: ProviderCallContext | None = None,
    ) -> LLMResponse:
        _ = temperature
        return await self._call_codex(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            on_content_delta=on_content_delta,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
            provider_context=provider_context,
        )

    async def chat_with_context(
        self,
        *,
        provider_context: ProviderCallContext,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self.chat(**kwargs, provider_context=provider_context)

    async def chat_stream_with_context(
        self,
        *,
        provider_context: ProviderCallContext,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self.chat_stream(**kwargs, provider_context=provider_context)

    def get_default_model(self) -> str:
        return self.default_model

    def can_resume_conversation_state(
        self,
        state: ProviderConversationState,
        model: str | None = None,
    ) -> bool:
        return responses_state_matches(
            state,
            provider=_responses_state_provider(),
            model=strip_model_prefix(model or self.default_model),
        )

    def supports_native_compaction(self, model: str | None = None) -> bool:
        """Codex compacts inline; disabled permanently once the backend rejects it."""
        _ = model
        return self._native_compaction_available

    # -- request pipeline --------------------------------------------------

    def _build_body(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        wire_model: str,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        state: ProviderConversationState | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        system_prompt, input_items, replayed = prepare_responses_input(
            messages,
            state=state,
            provider=_responses_state_provider(),
            model=wire_model,
        )

        body: dict[str, Any] = {
            "model": wire_model,
            "store": False,
            "stream": True,
            "instructions": system_prompt,
            "input": input_items,
            "include": ["reasoning.encrypted_content"],
            "text": {"verbosity": "medium"},
            "prompt_cache_key": _prompt_cache_key(messages[:2]),
            "tool_choice": tool_choice or "auto",
            "parallel_tool_calls": True,
        }

        reasoning_options = _build_reasoning_options(reasoning_effort)
        if replayed and "gpt-5.6" in wire_model.lower():
            reasoning_options = {**reasoning_options, "context": "all_turns"}
        if reasoning_options:
            body["reasoning"] = reasoning_options
        if tools:
            body["tools"] = convert_tools(tools)
        if self._extra_body:
            # Explicit provider overrides win, matching the other backends.
            body.update(self._extra_body)
        return body, input_items, replayed

    async def _call_codex(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        provider_context: ProviderCallContext | None = None,
    ) -> LLMResponse:
        """Shared request path for both ``chat`` and ``chat_stream``."""
        wire_model = strip_model_prefix(model or self.default_model)
        sanitized_messages = self._sanitize_empty_content(messages)
        state = provider_context.conversation_state if provider_context is not None else None
        if state is not None:
            state = state.with_pending_messages(
                self._sanitize_empty_content(state.pending_messages)
            )

        body, input_items, replayed = self._build_body(
            messages=sanitized_messages,
            tools=tools,
            wire_model=wire_model,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
            state=state,
        )

        stage = "oauth_token"
        try:
            token = await asyncio.to_thread(get_codex_token, self.proxy)
            headers = self._build_headers(token.account_id, token.access)

            async def _send(
                request_body: dict[str, Any],
                *,
                emit_deltas: bool,
            ) -> LLMResponse:
                return await _request_codex(
                    headers,
                    _without_response_item_ids(request_body),
                    proxy=self.proxy,
                    on_content_delta=on_content_delta if emit_deltas else None,
                    on_thinking_delta=on_thinking_delta if emit_deltas else None,
                    on_tool_call_delta=on_tool_call_delta if emit_deltas else None,
                )

            compact_threshold = resolve_compact_threshold(
                provider_context.context_window_tokens if provider_context is not None else None,
                max_tokens,
            )
            if (
                self.supports_native_compaction(wire_model)
                and replayed
                and state is not None
                and compact_threshold is not None
                and responses_state_context_tokens(state) >= compact_threshold
            ):
                stage = "codex_compaction"
                await self._apply_native_compaction(body, input_items, _send)

            stage = "codex_request"
            return await _send(body, emit_deltas=True)
        except Exception as exc:
            response = _codex_error_response(exc)
            exc_type = _exception_label(exc)
            logger.warning(
                "Codex API request failed: stage={} type={} kind={} retryable={} status={} "
                "error_type={} error_code={} retry_after={} summary={}",
                stage,
                exc_type,
                response.error_kind,
                response.error_should_retry,
                response.error_status_code,
                response.error_type,
                response.error_code,
                response.retry_after,
                _codex_log_summary(exc_type, response),
            )
            return response

    async def _apply_native_compaction(
        self,
        body: dict[str, Any],
        input_items: list[dict[str, Any]],
        send: Callable[..., Awaitable[LLMResponse]],
    ) -> None:
        """Ask the backend to compact, then continue from the compacted input.

        A failure here is never fatal: the turn proceeds uncompacted, and a
        genuine incompatibility disables the attempt for this provider's life.
        """
        compact_body = {**body, "input": [*input_items, {"type": "compaction_trigger"}]}
        try:
            result = await send(compact_body, emit_deltas=False)
            if result.finish_reason == "error":
                raise RuntimeError(result.content or "Codex compaction request failed")
            items = (
                responses_state_items(result.provider_state)
                if result.provider_state is not None
                else None
            )
            if not items or items[-1].get("type") not in _COMPACTION_ITEM_TYPES:
                raise RuntimeError("Codex compaction returned no compaction item")
            body["input"] = [*_retained_compaction_messages(input_items), *items]
        except Exception as exc:
            if is_compaction_compatibility_error(exc):
                self._native_compaction_available = False
            logger.warning(
                "Codex native compaction unavailable; continuing without it "
                "(type={} status={} disabled={})",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                not self._native_compaction_available,
            )

    def _build_headers(self, account_id: str | None, access_token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "OpenAI-Beta": "responses=experimental",
            "originator": CODEX_ORIGINATOR,
            "User-Agent": f"{CODEX_ORIGINATOR} (python)",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id
        headers.update(self._extra_headers)
        return headers


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def strip_model_prefix(model: str) -> str:
    """Drop the routing prefix so the backend receives its own model id."""
    for prefix in _MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _responses_state_provider() -> str:
    return f"openai_codex:{DEFAULT_CODEX_URL.rstrip('/')}"


def _prompt_cache_key(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_reasoning_options(reasoning_effort: str | None) -> dict[str, str]:
    """Opt in to visible summaries without overriding the backend's default effort."""
    if reasoning_effort and reasoning_effort.lower() == "none":
        return {"effort": "none"}
    options = {"summary": "auto"}
    if reasoning_effort:
        options["effort"] = reasoning_effort
    return options


def _without_response_item_ids(request_body: dict[str, Any]) -> dict[str, Any]:
    """Honor Codex's ``store=false`` contract, which rejects echoed item ids."""
    if request_body.get("store") is True:
        return request_body
    raw_input = request_body.get("input")
    if not isinstance(raw_input, list):
        return request_body

    sanitized: list[object] = []
    for raw_item in cast(list[object], raw_input):
        if not isinstance(raw_item, dict):
            sanitized.append(raw_item)
            continue
        item = cast(dict[str, Any], raw_item)
        sanitized.append({key: value for key, value in item.items() if key != "id"})

    body = dict(request_body)
    body["input"] = sanitized
    return body


def _retained_compaction_messages(
    input_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mirror Codex's bounded retention of user/developer/system messages."""
    retained_reversed: list[dict[str, Any]] = []
    remaining = _COMPACTION_RETAINED_CHAR_BUDGET
    for item in reversed(input_items):
        if item.get("type") not in {None, "message"} or item.get("role") not in {
            "user",
            "developer",
            "system",
        }:
            continue
        size = len(json.dumps(item, ensure_ascii=False, default=str))
        if size > remaining and retained_reversed:
            continue
        retained_reversed.append(item)
        remaining = max(0, remaining - size)
        if remaining == 0:
            break
    retained_reversed.reverse()
    return retained_reversed


class _CodexHTTPError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        should_retry: bool | None = None,
        compaction_unsupported: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.error_type = error_type
        self.error_code = error_code
        self.should_retry = should_retry
        self.compaction_unsupported = compaction_unsupported


async def _request_codex(
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    proxy: str | None = None,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> LLMResponse:
    """POST one Codex request and consume its SSE stream.

    TLS verification is never relaxed: the request carries a bearer token, so a
    certificate failure is a hard error rather than something to retry around.
    """
    client_kwargs: dict[str, Any] = {"timeout": resolve_stream_idle_timeout_s()}
    if proxy:
        client_kwargs["proxy"] = proxy
        client_kwargs["trust_env"] = False

    async with httpx.AsyncClient(**client_kwargs) as client:
        async with client.stream(
            "POST",
            DEFAULT_CODEX_URL,
            headers=headers,
            json=body,
        ) as response:
            if response.status_code != 200:
                raise _http_error_from_response(response, await response.aread())

            capture = ResponsesStreamCapture()
            (
                content,
                tool_calls,
                finish_reason,
                usage,
                reasoning_content,
            ) = await consume_sse_with_reasoning(
                response,
                on_content_delta=on_content_delta,
                on_tool_call_delta=on_tool_call_delta,
                on_reasoning_delta=on_thinking_delta,
                capture=capture,
            )
            result = LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                reasoning_content=reasoning_content,
            )
            if capture.completed and is_replayable_finish_reason(finish_reason):
                result.provider_state = build_responses_state(
                    provider=_responses_state_provider(),
                    model=str(body.get("model") or ""),
                    input_items=cast(list[dict[str, Any]], body.get("input") or []),
                    output_items=capture.output_items,
                    usage=usage,
                )
            return result


def _http_error_from_response(response: httpx.Response, payload: bytes) -> _CodexHTTPError:
    raw = payload.decode("utf-8", "ignore")
    status_code = response.status_code
    error_type, error_code = LLMProvider._extract_error_type_code(raw)  # pyright: ignore[reportPrivateUsage]
    lowered = raw.lower()
    return _CodexHTTPError(
        _friendly_error(status_code),
        status_code=status_code,
        retry_after=LLMProvider._extract_retry_after_from_headers(response.headers),  # pyright: ignore[reportPrivateUsage]
        error_type=error_type,
        error_code=error_code,
        should_retry=_should_retry_status(status_code, error_type, error_code, raw),
        compaction_unsupported=(
            status_code in {400, 404, 422}
            and any(marker in lowered for marker in _COMPACTION_MARKERS)
        ),
    )


def _friendly_error(status_code: int) -> str:
    """Describe a failure without echoing the upstream payload."""
    if status_code == 401:
        return f"Codex credentials were rejected (HTTP 401). {_LOGIN_HINT}"
    if status_code == 403:
        return f"Codex access denied (HTTP 403). {_LOGIN_HINT}"
    if status_code == 429:
        return "ChatGPT usage quota exceeded or rate limit triggered. Please try again later."
    return f"HTTP {status_code}: Codex API request failed"


def _exception_label(exc: Exception) -> str:
    return "CodexHTTPError" if isinstance(exc, _CodexHTTPError) else type(exc).__name__


def _codex_error_response(exc: Exception) -> LLMResponse:
    """Convert a Codex transport/API failure into retry-policy metadata."""
    exc_type = _exception_label(exc)
    detail = str(exc).strip()

    status_code = getattr(exc, "status_code", None)
    error_kind: str | None = None
    default_detail: str | None = None
    should_retry: bool | None = getattr(exc, "should_retry", None)

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        error_kind = "timeout"
        default_detail = "timed out waiting for response"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, httpx.RemoteProtocolError):
        error_kind = "connection"
        default_detail = "network protocol error while reading response"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, (httpx.NetworkError, httpx.TransportError)):
        error_kind = "connection"
        default_detail = "network connection failed"
        should_retry = True if should_retry is None else should_retry
    elif isinstance(exc, _CodexHTTPError):
        error_kind = "http"
        default_detail = "HTTP request failed"
    elif isinstance(exc, RuntimeError) and "credentials not found" in detail.lower():
        # oauth-cli-kit's signal that no credential is stored.
        error_kind = "auth"
        default_detail = "no Codex credential is stored"
        detail = f"{detail} {_LOGIN_HINT}".strip()
        should_retry = False

    if status_code is not None and should_retry is None:
        retry_content = (
            None if int(status_code) == 429 and isinstance(exc, _CodexHTTPError) else detail
        )
        should_retry = _should_retry_status(
            int(status_code),
            getattr(exc, "error_type", None),
            getattr(exc, "error_code", None),
            retry_content,
        )

    detail = detail or default_detail or "unexpected error"
    message = f"Error calling Codex ({exc_type}): {detail}"
    retry_after = getattr(exc, "retry_after", None) or LLMProvider._extract_retry_after(message)  # pyright: ignore[reportPrivateUsage]
    return LLMResponse(
        content=message,
        finish_reason="error",
        retry_after=retry_after,
        error_status_code=int(status_code) if status_code is not None else None,
        error_kind=error_kind,
        error_type=getattr(exc, "error_type", None),
        error_code=getattr(exc, "error_code", None),
        error_retry_after_s=retry_after,
        error_should_retry=should_retry,
    )


def _codex_log_summary(exc_type: str, response: LLMResponse) -> str:
    """Return a bounded summary — never the request body or upstream payload."""
    if response.error_status_code is not None:
        parts = [f"HTTP {response.error_status_code}"]
        if response.error_type:
            parts.append(f"type={response.error_type}")
        if response.error_code:
            parts.append(f"code={response.error_code}")
        return " ".join(parts)

    kind = (response.error_kind or "").strip()
    return f"{exc_type} {kind}" if kind else exc_type


def _should_retry_status(
    status_code: int,
    error_type: str | None,
    error_code: str | None,
    content: str | None,
) -> bool:
    if status_code == 429:
        return LLMProvider._is_retryable_429_response(  # pyright: ignore[reportPrivateUsage]
            LLMResponse(
                content=content or "",
                finish_reason="error",
                error_status_code=status_code,
                error_type=error_type,
                error_code=error_code,
            )
        )
    return (
        status_code in LLMProvider._RETRYABLE_STATUS_CODES  # pyright: ignore[reportPrivateUsage]
        or status_code >= 500
    )
