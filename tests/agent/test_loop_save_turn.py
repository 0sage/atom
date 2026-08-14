import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from atom.agent.context import ContextBuilder
from atom.agent.loop import AgentLoop
from atom.agent.tools.context import RequestContext, request_context
from atom.bus.events import InboundMessage
from atom.bus.queue import MessageBus
from atom.cron.session_turns import CRON_HISTORY_META, CRON_TRIGGER_META
from atom.providers.base import LLMProvider, LLMResponse, ProviderConversationState
from atom.providers.factory import ProviderSnapshot
from atom.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    append_runtime_context,
    public_history_message,
)
from atom.session.automation_turns import AUTOMATION_HISTORY_META
from atom.session.keys import (
    LAST_CHANNEL_METADATA_KEY,
    UNIFIED_SESSION_KEY,
)
from atom.session.manager import Session
from atom.triggers.local_session_turns import LOCAL_TRIGGER_META


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    from atom.config.schema import AgentDefaults

    loop.max_tool_result_chars = AgentDefaults().max_tool_result_chars
    # _persist_subagent_followup tokenizes injected text; off here so these
    # tests assert on the content they pass in.
    loop.tokenize_emails = False
    return loop


def _provider_state() -> ProviderConversationState:
    return ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={"items": []},
    )


def _runtime_message(content, blocks: list[RuntimeContextBlock]) -> dict:
    merged, marker = append_runtime_context(content, blocks)
    assert marker is not None
    return {
        "role": "user",
        "content": merged,
        "_meta": {RUNTIME_CONTEXT_MESSAGE_META: marker},
    }


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Test title"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    return loop


def test_agent_loop_llm_runtime_reflects_current_provider_and_model(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    runtime = loop.llm_runtime()

    assert runtime.provider is loop.provider
    assert runtime.model == "test-model"

    next_provider = MagicMock()
    next_provider.generation = SimpleNamespace(
        temperature=0.1,
        max_tokens=4096,
        reasoning_effort=None,
    )
    loop.runtime_resolver.adopt_snapshot(ProviderSnapshot(
        provider=next_provider,
        model="next-model",
        context_window_tokens=runtime.context_window_tokens,
        signature=("next-model",),
    ))
    runtime = loop.llm_runtime()

    assert runtime.provider is next_provider
    assert runtime.model == "next-model"


def test_persist_cron_turn_uses_distinct_history_marker(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("discord:auto")
    prompt_ref = {"id": "cron.agent_turn.reminder", "version": 1, "sha256": "abc"}

    persisted = loop._persist_user_message_early(
        InboundMessage(
            channel="discord",
            sender_id="cron",
            chat_id="auto",
            content="Cron job: internal prompt",
            metadata={
                CRON_TRIGGER_META: {
                    "job_id": "job-1",
                    "job_name": "Daily check",
                    "run_id": "job-1:1",
                    "prompt_ref": prompt_ref,
                    "persist_content": "Scheduled cron job triggered: Daily check",
                }
            },
        ),
        session,
    )

    assert persisted is True
    message = session.messages[-1]
    assert message["content"] == "Scheduled cron job triggered: Daily check"
    assert message[AUTOMATION_HISTORY_META] == {
        "kind": "cron",
        "cron_job_id": "job-1",
        "cron_job_name": "Daily check",
        "cron_run_id": "job-1:1",
        "cron_prompt_ref": prompt_ref,
    }
    assert message[CRON_HISTORY_META] is True
    assert CRON_TRIGGER_META not in message
    assert message["cron_job_id"] == "job-1"
    assert message["cron_job_name"] == "Daily check"
    assert message["cron_run_id"] == "job-1:1"
    assert message["cron_prompt_ref"] == prompt_ref


def test_persist_local_trigger_turn_uses_hidden_automation_marker(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("discord:auto")

    persisted = loop._persist_user_message_early(
        InboundMessage(
            channel="discord",
            sender_id="trigger",
            chat_id="auto",
            content="Review PR #4502",
            metadata={
                LOCAL_TRIGGER_META: {
                    "trigger_id": "trg_123",
                    "trigger_name": "PR review",
                    "delivery_id": "tdel_456",
                    "created_at_ms": 1_700_000_000_000,
                    "persist_content": "Local trigger received: PR review\n\nReview PR #4502",
                }
            },
        ),
        session,
    )

    assert persisted is True
    message = session.messages[-1]
    assert message["content"] == "Local trigger received: PR review\n\nReview PR #4502"
    assert message[AUTOMATION_HISTORY_META] == {
        "kind": "local_trigger",
        "trigger_id": "trg_123",
        "trigger_name": "PR review",
        "trigger_delivery_id": "tdel_456",
    }
    assert LOCAL_TRIGGER_META not in message
    assert message["trigger_id"] == "trg_123"
    assert message["trigger_name"] == "PR review"
    assert message["trigger_delivery_id"] == "tdel_456"


@pytest.mark.asyncio
async def test_new_with_bot_suffix_does_not_persist_command(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(
            channel="discord",
            sender_id="user",
            chat_id="chat-1",
            content="/new@atom_bot",
        )
    )

    assert response is not None
    assert response.content == "New session started."
    session = loop.sessions.get_or_create("discord:chat-1")
    assert session.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("/neaw", 'Unknown command "/neaw". Did you mean "/new"?'),
        (
            "/status now",
            'Command "/status" does not accept arguments. Did you mean "/status"?',
        ),
    ],
)
async def test_invalid_slash_command_is_rejected_without_calling_provider(
    tmp_path: Path,
    content: str,
    expected: str,
) -> None:
    loop = _make_full_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(
            channel="discord",
            sender_id="user",
            chat_id="chat-1",
            content=content,
        )
    )

    assert response is not None
    assert response.content == expected
    loop.provider.chat_with_retry.assert_not_awaited()
    session = loop.sessions.get_or_create("discord:chat-1")
    persisted = [
        (message["role"], message["content"], message.get("_command"))
        for message in session.messages
    ]
    assert persisted == [
        ("user", content, True),
        ("assistant", response.content, True),
    ]
def test_save_turn_keeps_multimodal_runtime_context_for_model_replay() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message([], [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "provider context"}
    ]
    assert public_history_message(session.messages[0])["content"] == []


def test_save_turn_keeps_image_placeholder_and_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/feishu/photo.jpg"}},
            ],
            [block],
        )],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image: /media/feishu/photo.jpg]"},
        {"type": "text", "text": "provider context"},
    ]
    assert public_history_message(session.messages[0])["content"] == [
        {"type": "text", "text": "[image: /media/feishu/photo.jpg]"}
    ]


def test_save_turn_keeps_image_placeholder_without_meta() -> None:
    loop = _mk_loop()
    session = Session(key="test:image-no-meta")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message(
            [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
            [block],
        )],
        skip=0,
    )
    assert session.messages[0]["content"] == [
        {"type": "text", "text": "[image]"},
        {"type": "text", "text": "provider context"},
    ]


def test_save_turn_persists_runtime_context_and_public_view_hides_it() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-strip")
    block = RuntimeContextBlock(source="host", content="internal host guidance")

    loop._save_turn(
        session,
        [_runtime_message("hello world", [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == "hello world\n\ninternal host guidance"
    assert session.messages[0][RUNTIME_CONTEXT_HISTORY_META]["sources"] == ["host"]
    assert public_history_message(session.messages[0])["content"] == "hello world"


def test_build_and_save_preserves_user_text_containing_guidance_tag(tmp_path: Path) -> None:
    loop = _mk_loop()
    session = Session(key="test:user-guidance-literal")
    user_text = (
        "Keep this prefix\n"
        "[Runtime Guidance — host instructions]\n"
        "This label and everything after it are user-authored."
    )
    messages = ContextBuilder(tmp_path).build_messages(
        [],
        user_text,
        channel="cli",
    )
    assert "_meta" not in messages[-1]

    loop._save_turn(session, messages, skip=1)

    assert session.messages[0]["content"] == user_text


def test_build_and_save_preserves_multimodal_user_block_starting_with_runtime_tag(
    tmp_path: Path,
) -> None:
    loop = _mk_loop()
    session = Session(key="test:user-runtime-literal-block")
    image = tmp_path / "user-tag.png"
    image.write_bytes(_PNG_1X1)
    user_text = (
        f"{ContextBuilder._RUNTIME_CONTEXT_TAG}\n"
        "This entire block is user-authored and must remain in history."
    )
    messages = ContextBuilder(tmp_path).build_messages(
        [],
        user_text,
        media=[str(image)],
        channel="cli",
    )

    loop._save_turn(session, messages, skip=1)

    assert {"type": "text", "text": user_text} in session.messages[0]["content"]


def test_save_turn_keeps_string_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-only")
    block = RuntimeContextBlock(source="test", content="provider context")

    loop._save_turn(
        session,
        [_runtime_message("", [block])],
        skip=0,
    )
    assert session.messages[0]["content"] == "provider context"
    assert public_history_message(session.messages[0])["content"] == ""


def test_save_turn_keeps_tool_results_under_16k() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool-result")
    content = "x" * 12_000

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": content},
        ],
        skip=0,
    )

    assert session.messages[1]["content"] == content


def test_save_turn_stamps_latency_on_last_assistant() -> None:
    loop = _mk_loop()
    session = Session(key="test:latency")

    loop._save_turn(
        session,
        [
            {"role": "assistant", "content": "hello", "tool_calls": [{"id": "c1"}]},
            {"role": "assistant", "content": "final answer"},
        ],
        skip=0,
        turn_latency_ms=12345,
    )

    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "final answer"
    assert session.messages[-1]["latency_ms"] == 12345


def test_restore_runtime_checkpoint_rehydrates_completed_and_pending_tools() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint",
        provider_state=_provider_state(),
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"
    assert "interrupted before this tool finished" in session.messages[2]["content"].lower()
    assert session.provider_state is None


def test_restore_final_response_checkpoint_preserves_matching_provider_state() -> None:
    loop = _mk_loop()
    state = _provider_state()
    session = Session(
        key="test:final-checkpoint",
        provider_state=state,
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "phase": "final_response",
                AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION_KEY: (
                    AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION
                ),
                "assistant_message": {
                    "role": "assistant",
                    "content": "finished",
                },
                "completed_tool_results": [],
                "pending_tool_calls": [],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.messages[-1]["content"] == "finished"
    assert session.provider_state is state
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None


def test_restore_legacy_final_checkpoint_discards_unproven_provider_state() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:legacy-final-checkpoint",
        provider_state=_provider_state(),
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "phase": "final_response",
                "assistant_message": {
                    "role": "assistant",
                    "content": "finished",
                },
                "completed_tool_results": [],
                "pending_tool_calls": [],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.messages[-1]["content"] == "finished"
    assert session.provider_state is None


def test_restore_completed_tools_checkpoint_preserves_matching_provider_state() -> None:
    loop = _mk_loop()
    tool_result = {
        "role": "tool",
        "tool_call_id": "call_done",
        "name": "read_file",
        "content": "compacted result",
    }
    state = _provider_state().with_pending_messages([tool_result])
    session = Session(
        key="test:completed-tools-checkpoint",
        provider_state=state,
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "phase": "tools_completed",
                AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION_KEY: (
                    AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION
                ),
                "assistant_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                "completed_tool_results": [tool_result],
                "pending_tool_calls": [],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.messages[-1]["content"] == "compacted result"
    assert session.provider_state is state


def test_restore_runtime_checkpoint_dedupes_overlapping_tail() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint-overlap",
        messages=[
            {
                "role": "assistant",
                "content": "working",
                "tool_calls": [
                    {
                        "id": "call_done",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_done",
                "name": "read_file",
                "content": "ok",
            },
        ],
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert len(session.messages) == 3
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"


@pytest.mark.asyncio
async def test_runtime_checkpoint_keeps_provider_state_out_of_public_metadata(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    state = ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={
            "items": [
                {
                    "type": "reasoning",
                    "encrypted_content": "private-checkpoint-blob",
                }
            ]
        },
    )
    loop.provider.can_resume_conversation_state.return_value = True
    loop.provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", provider_state=state)
    )
    session = loop.sessions.get_or_create("cli:private-checkpoint")

    await loop._run_agent_loop(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
        ],
        runtime=loop.llm_runtime(),
        session=session,
    )

    assert session.provider_state is not None
    checkpoint = session.metadata[AgentLoop._RUNTIME_CHECKPOINT_KEY]
    assert "provider_state" not in checkpoint
    assert checkpoint[AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION_KEY] == (
        AgentLoop._PROVIDER_STATE_CHECKPOINT_VERSION
    )
    assert "private-checkpoint-blob" not in json.dumps(session.metadata)

    public_payload = loop.sessions.read_session_file(session.key)
    assert public_payload is not None
    assert "private-checkpoint-blob" not in json.dumps(public_payload)
    raw = loop.sessions._get_session_path(session.key).read_text(encoding="utf-8")
    assert "private-checkpoint-blob" in raw


@pytest.mark.asyncio
async def test_process_message_persists_user_message_before_turn_completes(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="persist me")
    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate("feishu:c1")
    persisted = loop.sessions.get_or_create("feishu:c1")
    assert [m["role"] for m in persisted.messages] == ["user"]
    assert persisted.messages[0]["content"] == "persist me"
    assert persisted.metadata.get(AgentLoop._PENDING_USER_TURN_KEY) is True
    assert persisted.updated_at >= persisted.created_at


@pytest.mark.asyncio
async def test_subagent_followup_stages_provider_state_before_turn_runs(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    loop.provider.can_resume_conversation_state.return_value = True
    session = loop.sessions.get_or_create("cli:subagent-crash")
    session.provider_state = _provider_state()
    loop.sessions.save(session)

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:subagent-crash",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )
    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate("cli:subagent-crash")
    persisted = loop.sessions.get_or_create("cli:subagent-crash")
    assert persisted.messages[-1]["content"] == "subagent result"
    assert persisted.provider_state is not None
    assert persisted.provider_state.pending_messages[-1]["role"] == "user"
    assert persisted.provider_state.pending_messages[-1]["content"] == "subagent result"


@pytest.mark.asyncio
async def test_subagent_followup_state_is_durable_before_prompt_assembly(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.can_resume_conversation_state.return_value = True
    loop._build_initial_messages = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("prompt boom"),
    )
    session = loop.sessions.get_or_create("cli:subagent-prompt-crash")
    session.provider_state = _provider_state()
    loop.sessions.save(session)

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:subagent-prompt-crash",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )
    with pytest.raises(RuntimeError, match="prompt boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate("cli:subagent-prompt-crash")
    persisted = loop.sessions.get_or_create("cli:subagent-prompt-crash")
    assert persisted.messages[-1]["content"] == "subagent result"
    assert persisted.provider_state is not None
    assert persisted.provider_state.pending_messages[-1]["content"] == (
        "subagent result"
    )


@pytest.mark.asyncio
async def test_subagent_redelivery_does_not_duplicate_staged_provider_input(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.can_resume_conversation_state.return_value = True
    build_initial_messages = loop._build_initial_messages
    loop._build_initial_messages = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("prompt boom"),
    )
    session = loop.sessions.get_or_create("cli:subagent-redelivery")
    session.provider_state = _provider_state()
    loop.sessions.save(session)
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:subagent-redelivery",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )

    with pytest.raises(RuntimeError, match="prompt boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate("cli:subagent-redelivery")
    persisted = loop.sessions.get_or_create("cli:subagent-redelivery")
    assert persisted.provider_state is not None
    assert [
        message.get("content")
        for message in persisted.provider_state.pending_messages
    ].count("subagent result") == 1
    loop._build_initial_messages = build_initial_messages  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("provider boom"),
    )
    with pytest.raises(RuntimeError, match="provider boom"):
        await loop._process_message(msg)

    provider_state = loop._run_agent_loop.await_args.kwargs["provider_state"]
    assert provider_state is not None
    pending_results = [
        message
        for message in provider_state.pending_messages
        if message.get("content") == "subagent result"
    ]
    assert len(pending_results) == 1
    assert LLMProvider._sanitize_empty_content(pending_results) == [
        {"role": "user", "content": "subagent result"},
    ]


@pytest.mark.asyncio
async def test_subagent_followup_clears_state_before_compatibility_failure(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.can_resume_conversation_state.side_effect = RuntimeError(
        "compatibility boom"
    )
    session = loop.sessions.get_or_create("cli:subagent-compat-crash")
    session.provider_state = _provider_state()
    loop.sessions.save(session)

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:subagent-compat-crash",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )
    with pytest.raises(RuntimeError, match="compatibility boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate("cli:subagent-compat-crash")
    persisted = loop.sessions.get_or_create("cli:subagent-compat-crash")
    assert persisted.messages[-1]["content"] == "subagent result"
    assert persisted.provider_state is None


@pytest.mark.asyncio
async def test_process_message_persists_unified_session_delivery_route(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop._unified_session = True
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="oc_123",
        content="persist my route",
        session_key_override=UNIFIED_SESSION_KEY,
    )
    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    persisted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert persisted.metadata[LAST_CHANNEL_METADATA_KEY] == "feishu:oc_123"


@pytest.mark.parametrize(
    ("msg", "is_user_turn"),
    [
        (
            InboundMessage(
                channel="cli",
                sender_id="u1",
                chat_id="direct",
                content="cli input",
            ),
            True,
        ),
        (
            InboundMessage(
                channel="system",
                sender_id="system",
                chat_id="discord:automation",
                content="system event",
            ),
            False,
        ),
        (
            InboundMessage(
                channel="discord",
                sender_id="subagent",
                chat_id="subagent-result",
                content="subagent result",
            ),
            True,
        ),
        (
            InboundMessage(
                channel="discord",
                sender_id="u1",
                chat_id="automation",
                content="scheduled turn",
                metadata={CRON_TRIGGER_META: {"job_id": "job-1"}},
            ),
            True,
        ),
    ],
)
def test_unified_session_route_ignores_non_user_destinations(
    tmp_path: Path,
    msg: InboundMessage,
    is_user_turn: bool,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop._unified_session = True
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.metadata[LAST_CHANNEL_METADATA_KEY] = "telegram:existing"

    loop._remember_unified_session_route(session, msg, is_user_turn=is_user_turn)

    assert session.metadata[LAST_CHANNEL_METADATA_KEY] == "telegram:existing"


# 1x1 PNG used by the media-persistence tests. Attachment preparation filters
# ``msg.media`` down to paths that magic-byte-sniff as images, so the test
# fixture needs real bytes on disk (not just placeholder paths).
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x00\x00\x02\x00\x01"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_process_message_persists_media_paths_on_user_turn(tmp_path: Path) -> None:
    """User turns that attach images must record the media paths alongside the
    text so session replay can restore attachment references from history.
    """
    img_a = tmp_path / "uuid-1.png"
    img_a.write_bytes(_PNG_1X1)
    img_b = tmp_path / "uuid-2.png"
    img_b.write_bytes(_PNG_1X1)

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("interrupt"))  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="discord",
        sender_id="u1",
        chat_id="c-media",
        content="look",
        media=[str(img_a), str(img_b)],
    )
    with pytest.raises(RuntimeError, match="interrupt"):
        await loop._process_message(msg)

    loop.sessions.invalidate("discord:c-media")
    persisted = loop.sessions.get_or_create("discord:c-media")
    assert [m["role"] for m in persisted.messages] == ["user"]
    assert persisted.messages[0]["content"] == "look"
    assert persisted.messages[0]["media"] == [str(img_a), str(img_b)]


@pytest.mark.asyncio
async def test_process_message_persists_media_only_turn_without_text(tmp_path: Path) -> None:
    """A turn with images but no text still persists (previously silent-dropped).

    The old early-persist gate skipped messages without text, leaving pure
    image turns un-checkpointed. They now materialise as an empty-content
    user row with ``media`` attached.
    """
    img = tmp_path / "only.png"
    img.write_bytes(_PNG_1X1)

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="discord",
        sender_id="u1",
        chat_id="c-images-only",
        content="",
        media=[str(img)],
    )
    with pytest.raises(RuntimeError):
        await loop._process_message(msg)

    loop.sessions.invalidate("discord:c-images-only")
    persisted = loop.sessions.get_or_create("discord:c-images-only")
    assert len(persisted.messages) == 1
    assert persisted.messages[0]["role"] == "user"
    assert persisted.messages[0]["content"] == ""
    assert persisted.messages[0]["media"] == [str(img)]


@pytest.mark.asyncio
async def test_process_message_does_not_duplicate_early_persisted_user_message(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=(
        "done",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c2", content="hello")
    )

    assert result is not None
    assert result.content == "done"
    session = loop.sessions.get_or_create("feishu:c2")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "done"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata


@pytest.mark.asyncio
async def test_process_message_keeps_delivery_chat_for_thread_session(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.context.build_messages = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
        ]
    )
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "done",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))

    result = await loop._process_message(
        InboundMessage(
            channel="discord",
            sender_id="u1",
            chat_id="thread-777",
            content="hello",
            metadata={"context_chat_id": "parent-456"},
            session_key_override="discord:parent-456:thread:thread-777",
        )
    )

    assert result is not None
    assert result.chat_id == "thread-777"
    assert loop._run_agent_loop.call_args.kwargs["chat_id"] == "thread-777"


@pytest.mark.asyncio
async def test_process_direct_rejects_reserved_system_channel(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop._process_message = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="reserved for internal messages"):
        await loop.process_direct("external input", channel="system")

    loop._process_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_direct_skip_user_persist_does_not_save_retry_user(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("api:default")
    session.add_message("user", "hello")
    session.add_message("assistant", "previous empty-response attempt")
    loop.sessions.save(session)

    await loop.process_direct(
        "hello",
        session_key=session.key,
        channel="api",
        chat_id="default",
        persist_user_message=False,
    )

    session = loop.sessions.get_or_create("api:default")
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "hello"),
        ("assistant", "previous empty-response attempt"),
        ("assistant", "Test title"),
    ]


@pytest.mark.asyncio
async def test_request_context_uses_effective_key_for_spawn_tool(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None
    spawn_tool._manager.spawn = AsyncMock(return_value="started")  # type: ignore[attr-defined]
    runtime = loop.llm_runtime()

    with request_context(RequestContext(
        channel="discord",
        chat_id="thread-777",
        session_key="discord:parent-456:thread:thread-777",
        runtime=runtime,
    )):
        await spawn_tool.execute(task="inspect context")

    call = spawn_tool._manager.spawn.await_args.kwargs  # type: ignore[attr-defined]
    assert call["origin_channel"] == "discord"
    assert call["origin_chat_id"] == "thread-777"
    assert call["session_key"] == "discord:parent-456:thread:thread-777"
    assert call["runtime"] is runtime


@pytest.mark.asyncio
async def test_next_turn_after_crash_closes_pending_user_turn_before_new_input(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.chat_with_retry = AsyncMock(return_value=MagicMock())  # unused because _run_agent_loop is stubbed

    session = loop.sessions.get_or_create("feishu:c3")
    session.add_message("user", "old question")
    session.metadata[AgentLoop._PENDING_USER_TURN_KEY] = True
    session.provider_state = _provider_state().with_pending_messages([
        {"role": "user", "content": "old question"},
    ])
    loop.sessions.save(session)

    loop._run_agent_loop = AsyncMock(return_value=(
        "new answer",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c3", content="new question")
    )

    assert result is not None
    assert result.content == "new answer"
    session = loop.sessions.get_or_create("feishu:c3")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata
    assert session.provider_state is None


@pytest.mark.asyncio
async def test_stop_preserves_runtime_checkpoint_for_next_turn(tmp_path: Path) -> None:
    from atom.command.builtin import cmd_stop
    from atom.command.router import CommandContext

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    checkpoint_saved = asyncio.Event()

    async def interrupted_run_agent_loop(_initial_messages, *, session=None, **_kwargs):
        assert session is not None
        loop._set_runtime_checkpoint(
            session,
            {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec", "arguments": "{}"},
                    }
                ],
            },
        )
        checkpoint_saved.set()
        await asyncio.Event().wait()

    loop._run_agent_loop = interrupted_run_agent_loop  # type: ignore[method-assign]

    first_msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="keep progress")
    task = asyncio.create_task(loop._process_message(first_msg))
    loop._active_tasks[first_msg.session_key] = {task}
    await asyncio.wait_for(checkpoint_saved.wait(), timeout=1.0)

    stop_msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="/stop")
    stop_ctx = CommandContext(msg=stop_msg, session=None, key=stop_msg.session_key, raw="/stop", loop=loop)
    stop_result = await cmd_stop(stop_ctx)

    assert "Stopped 1 task" in stop_result.content
    assert task.done()

    loop.sessions.invalidate("feishu:c4")
    interrupted = loop.sessions.get_or_create("feishu:c4")
    assert interrupted.metadata.get(AgentLoop._PENDING_USER_TURN_KEY) is True
    assert interrupted.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is not None

    async def resumed_run_agent_loop(initial_messages, **_kwargs):
        return (
            "next answer",
            None,
            [*initial_messages, {"role": "assistant", "content": "next answer"}],
            "stop",
            False,
        )

    loop._run_agent_loop = resumed_run_agent_loop  # type: ignore[method-assign]
    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="continue here")
    )

    assert result is not None
    assert result.content == "next answer"

    session = loop.sessions.get_or_create("feishu:c4")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content", "tool_call_id", "name"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "keep progress"},
        {"role": "assistant", "content": "working"},
        {"role": "tool", "tool_call_id": "call_done", "name": "read_file", "content": "ok"},
        {
            "role": "tool",
            "tool_call_id": "call_pending",
            "name": "exec",
            "content": "Error: Task interrupted before this tool finished.",
        },
        {"role": "user", "content": "continue here"},
        {"role": "assistant", "content": "next answer"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata
    assert AgentLoop._RUNTIME_CHECKPOINT_KEY not in session.metadata


@pytest.mark.asyncio
async def test_system_subagent_followup_is_persisted_before_prompt_assembly(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("cli:test")
    session.add_message("user", "question")
    session.add_message("assistant", "working")
    loop.sessions.save(session)

    runtime = loop.llm_runtime()
    seen: dict[str, object] = {}
    record_runtime = MagicMock(wraps=loop.runtime_event_publisher.record_turn_runtime)
    loop.runtime_event_publisher.record_turn_runtime = record_runtime

    async def fake_run_agent_loop(initial_messages, **kwargs):
        seen["initial_messages"] = initial_messages
        seen["runtime"] = kwargs["runtime"]
        seen["request_context"] = kwargs["request_context"]
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:test",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        ),
        runtime=runtime,
    )

    assert seen["runtime"] is runtime
    request = seen["request_context"]
    assert isinstance(request, RequestContext)
    assert request.channel == "cli"
    assert request.chat_id == "test"
    assert request.session_key == "cli:test"
    assert request.original_user_text is None
    assert request.sender_id == "subagent"
    assert request.metadata == {"subagent_task_id": "sub-1"}
    assert request.turn_id
    record_runtime.assert_called_once_with("cli:test", runtime)
    assert len(loop.consolidator.maybe_consolidate_by_tokens.call_args_list) == 2
    assert all(
        call.kwargs["runtime"] is runtime
        for call in loop.consolidator.maybe_consolidate_by_tokens.call_args_list
    )
    initial_messages = seen["initial_messages"]
    assert isinstance(initial_messages, list)
    non_system = [m for m in initial_messages if m.get("role") != "system"]
    assert "question" in non_system[0]["content"]
    assert "working" in non_system[1]["content"]
    # Persisted timestamps stay in session records, but replay content is not
    # rewritten with volatile ``[Message Time: ...]`` prefixes.
    assert "[Message Time:" not in non_system[0]["content"]
    assert "[Message Time:" not in non_system[1]["content"]
    assert non_system[2]["role"] == "user"
    assert non_system[2]["content"].count("subagent result") == 1
    assert non_system[2]["content"] == "subagent result"

    loop.sessions.invalidate("cli:test")
    persisted = loop.sessions.get_or_create("cli:test")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content", "injected_event", "subagent_task_id"}}
        for m in persisted.messages
    ] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "working"},
        {
            "role": "assistant",
            "content": "subagent result",
            "injected_event": "subagent_result",
            "subagent_task_id": "sub-1",
        },
        {"role": "assistant", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_system_subagent_followup_does_not_log_content(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    secret = "LEAKME42"
    content = f"[Subagent 'research' completed]\n\nTask: inspect logs\n\nResult:\n{secret}"
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")

    try:
        await loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="cli:logs",
                content=content,
                metadata={"subagent_task_id": "sub-logs"},
            )
        )
    finally:
        logger.remove(sink_id)

    logged = "".join(logs)
    assert "Processing system message from subagent" in logged
    assert secret not in logged


@pytest.mark.asyncio
async def test_system_subagent_followup_uses_common_turn_lifecycle(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )
    visited: list[str] = []

    for name in (
        "_restore_turn",
        "_compact_session",
        "_dispatch_command",
        "_build_turn",
        "_run_turn",
        "_persist_turn",
        "_prepare_outbound",
    ):
        original = getattr(loop, name)

        async def record(ctx, *, _original=original, _name=name):
            visited.append(_name)
            return await _original(ctx)

        setattr(loop, name, record)

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    logs: list[str] = []
    sink_id = logger.add(logs.append, level="DEBUG", format="{message}")
    try:
        await loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="cli:test",
                content="subagent result",
                metadata={"subagent_task_id": "sub-1"},
            )
        )
    finally:
        logger.remove(sink_id)

    assert visited == [
        "_restore_turn",
        "_compact_session",
        "_dispatch_command",
        "_build_turn",
        "_run_turn",
        "_persist_turn",
        "_prepare_outbound",
    ]
    logged = "".join(logs)
    for stage in ("restore", "compact", "command", "build", "run", "save", "respond"):
        assert f"Stage {stage} completed in" in logged


@pytest.mark.asyncio
async def test_multiple_subagent_followups_all_persist_as_standalone_history(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (
            "ack",
            [],
            [*initial_messages, {"role": "assistant", "content": "ack"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    for idx in range(3):
        await loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="cli:multi",
                content=f"subagent result {idx}",
                metadata={"subagent_task_id": f"sub-{idx}"},
            )
        )

    loop.sessions.invalidate("cli:multi")
    persisted = loop.sessions.get_or_create("cli:multi")
    followups = [m for m in persisted.messages if m.get("injected_event") == "subagent_result"]
    assert [m["content"] for m in followups] == [
        "subagent result 0",
        "subagent result 1",
        "subagent result 2",
    ]


def test_subagent_followup_uses_user_model_input_and_assistant_history(tmp_path: Path) -> None:
    loop = _mk_loop()
    session = Session(key="cli:merge")
    session.add_message("assistant", "previous assistant")
    history = session.get_history(max_messages=0)

    inserted = loop._persist_subagent_followup(
        session,
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:merge",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        ),
    )

    assert inserted is True

    builder = ContextBuilder(tmp_path)
    projected = builder.build_messages(
        history=history,
        current_message="subagent result",
        channel="cli",
    )

    non_system = [m for m in projected if m.get("role") != "system"]
    assert len(non_system) == 2
    assert non_system[-1]["role"] == "user"
    assert "subagent result" in non_system[-1]["content"]
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "subagent result"
    assert session.messages[-1]["injected_event"] == "subagent_result"


def test_subagent_followup_dedupes_by_task_id() -> None:
    loop = _mk_loop()
    session = Session(key="cli:dedupe")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:dedupe",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )

    assert loop._persist_subagent_followup(session, msg) is True
    assert loop._persist_subagent_followup(session, msg) is False
    assert len(session.messages) == 1


def test_subagent_followup_skips_empty_content() -> None:
    loop = _mk_loop()
    session = Session(key="cli:empty")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:empty",
        content="",
        metadata={"subagent_task_id": "sub-empty"},
    )

    assert loop._persist_subagent_followup(session, msg) is False
    assert session.messages == []


@pytest.mark.asyncio
async def test_request_context_passes_thread_session_key_to_spawn(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None
    spawn_tool._manager.spawn = AsyncMock(return_value="started")  # type: ignore[attr-defined]
    runtime = loop.llm_runtime()

    with request_context(RequestContext(
        channel="slack",
        chat_id="C123",
        message_id="msg-123",
        metadata={"slack": {"thread_ts": "1700.42", "channel_type": "channel"}},
        session_key="slack:C123:1700.42",
        runtime=runtime,
    )):
        await spawn_tool.execute(task="inspect thread")

    call = spawn_tool._manager.spawn.await_args.kwargs  # type: ignore[attr-defined]
    assert call["session_key"] == "slack:C123:1700.42"
    assert call["origin_message_id"] == "msg-123"
    assert call["runtime"] is runtime


@pytest.mark.asyncio
async def test_system_subagent_followup_uses_thread_session_and_slack_metadata(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    thread_session = loop.sessions.get_or_create("slack:C123:1700.42")
    thread_session.add_message("user", "thread question")
    loop.sessions.save(thread_session)

    seen: dict[str, object] = {}

    async def fake_run_agent_loop(initial_messages, **kwargs):
        seen["initial_messages"] = initial_messages
        seen["request_context"] = kwargs["request_context"]
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    outbound = await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="slack:C123",
            content="subagent result",
            session_key_override="slack:C123:1700.42",
            metadata={"subagent_task_id": "sub-1", "origin_message_id": "msg-123"},
        )
    )

    assert outbound is not None
    assert outbound.channel == "slack"
    assert outbound.chat_id == "C123"
    assert outbound.metadata == {
        "slack": {"thread_ts": "1700.42"},
        "origin_message_id": "msg-123",
    }
    request = seen["request_context"]
    assert isinstance(request, RequestContext)
    assert request.channel == "slack"
    assert request.chat_id == "C123"
    assert request.metadata == {
        "subagent_task_id": "sub-1",
        "origin_message_id": "msg-123",
    }
    assert "slack" not in request.metadata
    initial_messages = seen["initial_messages"]
    assert isinstance(initial_messages, list)
    assert "thread question" in initial_messages[1]["content"]

    loop.sessions.invalidate("slack:C123:1700.42")
    persisted = loop.sessions.get_or_create("slack:C123:1700.42")
    assert any(m.get("subagent_task_id") == "sub-1" for m in persisted.messages)


@pytest.mark.asyncio
async def test_turn_after_unanswered_user_keeps_tool_call_pairing(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("feishu:c-merge")
    session.add_message("user", "earlier question that never got an answer")
    loop.sessions.save(session)

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        assert [m["role"] for m in initial_messages] == ["system", "user"]
        return (
            "done",
            [],
            [
                *initial_messages,
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_ls",
                        "type": "function",
                        "function": {"name": "exec", "arguments": '{"command": "ls"}'},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_ls", "name": "exec", "content": "file.txt"},
                {"role": "assistant", "content": "done"},
            ],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(
            channel="feishu", sender_id="u1", chat_id="c-merge", content="and another thing"
        )
    )

    assert result is not None
    loop.sessions.invalidate("feishu:c-merge")
    persisted = loop.sessions.get_or_create("feishu:c-merge")

    declared: set[str] = set()
    for message in persisted.messages:
        if message.get("role") == "assistant":
            declared.update(
                str(tc["id"]) for tc in message.get("tool_calls") or [] if tc.get("id")
            )
        if message.get("role") == "tool":
            assert str(message.get("tool_call_id")) in declared, (
                f"orphaned tool result {message.get('tool_call_id')!r}: "
                f"{[m.get('role') for m in persisted.messages]}"
            )
    assert [m["role"] for m in persisted.messages] == [
        "user", "user", "assistant", "tool", "assistant",
    ]


def test_save_turn_keeps_placeholder_for_empty_tool_result_blocks() -> None:
    loop = _mk_loop()
    session = Session(key="test:empty-tool-blocks")

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_empty",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_empty", "name": "exec", "content": []},
        ],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]
    assert session.messages[1]["content"] == [
        {"type": "text", "text": "[tool result omitted during persistence]"}
    ]


def test_save_turn_drops_orphaned_tool_results() -> None:
    loop = _mk_loop()
    session = Session(key="test:orphan-guard")
    session.add_message("user", "hi")

    loop._save_turn(
        session,
        [
            {"role": "tool", "tool_call_id": "call_ghost", "name": "exec", "content": "boo"},
            {"role": "assistant", "content": "done"},
        ],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["user", "assistant"]


def test_save_turn_drops_tool_results_without_tool_call_id() -> None:
    loop = _mk_loop()
    session = Session(key="test:missing-tool-call-id")
    session.add_message("user", "hi")

    loop._save_turn(
        session,
        [
            {"role": "tool", "name": "exec", "content": "missing id"},
            {"role": "assistant", "content": "done"},
        ],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["user", "assistant"]


def test_save_turn_keeps_tool_results_declared_in_prior_history() -> None:
    loop = _mk_loop()
    session = Session(key="test:prior-declared")
    session.add_message(
        "assistant",
        "working",
        tool_calls=[{
            "id": "call_prior",
            "type": "function",
            "function": {"name": "exec", "arguments": "{}"},
        }],
    )

    loop._save_turn(
        session,
        [{"role": "tool", "tool_call_id": "call_prior", "name": "exec", "content": "ok"}],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]


def test_save_turn_drops_tool_result_already_fulfilled_in_history() -> None:
    loop = _mk_loop()
    session = Session(key="test:prior-fulfilled")
    session.add_message(
        "assistant",
        "",
        tool_calls=[{
            "id": "call_prior",
            "type": "function",
            "function": {"name": "exec", "arguments": "{}"},
        }],
    )
    session.add_message(
        "tool",
        "first",
        tool_call_id="call_prior",
        name="exec",
    )

    loop._save_turn(
        session,
        [{"role": "tool", "tool_call_id": "call_prior", "name": "exec", "content": "duplicate"}],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]
    assert session.messages[1]["content"] == "first"


def test_save_turn_drops_duplicate_tool_result_ids() -> None:
    loop = _mk_loop()
    session = Session(key="test:duplicate-tool-result")

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_dupe",
                    "type": "function",
                    "function": {"name": "exec", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_dupe", "name": "exec", "content": "first"},
            {"role": "tool", "tool_call_id": "call_dupe", "name": "exec", "content": "second"},
        ],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]
    assert session.messages[1]["content"] == "first"
