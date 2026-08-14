from __future__ import annotations

from atom.bus.events import OutboundMessage
from atom.bus.outbound_events import (
    ProgressEvent,
    RetryWaitEvent,
    RuntimeModelUpdatedEvent,
    SessionUpdatedEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    TurnEndEvent,
    outbound_event_from_message,
    outbound_message_for_event,
    replace_outbound_event,
)


def test_progress_event_lives_on_outbound_message_event_field() -> None:
    tool_events = [{"phase": "start", "name": "read_file"}]
    file_edit_events = [{"phase": "end", "path": "app.py"}]

    msg = outbound_message_for_event(
        channel="websocket",
        chat_id="chat-1",
        event=ProgressEvent(
            content="working",
            tool_hint=True,
            reasoning_delta=True,
            stream_id="r1",
            tool_events=tool_events,
            file_edit_events=file_edit_events,
        ),
        metadata={"origin_message_id": "m1"},
    )

    assert msg.content == "working"
    assert msg.metadata == {"origin_message_id": "m1"}

    event = outbound_event_from_message(msg)
    assert isinstance(event, ProgressEvent)
    assert event.content == "working"
    assert event.tool_hint is True
    assert event.reasoning_delta is True
    assert event.stream_id == "r1"
    assert event.tool_events == tool_events
    assert event.file_edit_events == file_edit_events


def test_normal_outbound_message_has_no_runtime_event() -> None:
    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello")

    assert outbound_event_from_message(msg) is None


def test_legacy_progress_metadata_flags_create_runtime_event() -> None:
    tool_events = [{"phase": "start", "name": "read_file"}]
    file_edit_events = [{"phase": "end", "path": "app.py"}]
    msg = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="legacy progress",
        metadata={
            "_progress": True,
            "_tool_hint": True,
            "_reasoning_delta": True,
            "_stream_id": "r1",
            "_tool_events": tool_events,
            "_file_edit_events": file_edit_events,
            "message_id": "platform-routing-context",
        },
    )

    event = outbound_event_from_message(msg)
    assert isinstance(event, ProgressEvent)
    assert event.content == "legacy progress"
    assert event.tool_hint is True
    assert event.reasoning_delta is True
    assert event.stream_id == "r1"
    assert event.tool_events == tool_events
    assert event.file_edit_events == file_edit_events


def test_legacy_stream_metadata_flags_create_runtime_events() -> None:
    delta = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="hello",
        metadata={"_stream_delta": True, "_stream_id": "s1"},
    )
    end = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={
            "_stream_end": True,
            "_stream_id": "s1",
            "_resuming": True,
            "_merge_next": True,
        },
    )

    delta_event = outbound_event_from_message(delta)
    assert isinstance(delta_event, StreamDeltaEvent)
    assert delta_event.content == "hello"
    assert delta_event.stream_id == "s1"

    end_event = outbound_event_from_message(end)
    assert isinstance(end_event, StreamEndEvent)
    assert end_event.stream_id == "s1"
    assert end_event.resuming is True
    assert end_event.merge_next is True


def test_legacy_webui_runtime_metadata_flags_create_runtime_events() -> None:
    runtime = OutboundMessage(
        channel="websocket",
        chat_id="*",
        content="",
        metadata={
            "_runtime_model_updated": True,
            "model": "gpt-5.5",
            "model_preset": "high",
        },
    )
    turn_end = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_turn_end": True, "latency_ms": 42.0},
    )
    session_updated = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_session_updated": True, "_session_update_scope": "metadata"},
    )

    runtime_event = outbound_event_from_message(runtime)
    assert isinstance(runtime_event, RuntimeModelUpdatedEvent)
    assert runtime_event.model == "gpt-5.5"
    assert runtime_event.model_preset == "high"

    turn_end_event = outbound_event_from_message(turn_end)
    assert isinstance(turn_end_event, TurnEndEvent)
    assert turn_end_event.latency_ms == 42

    session_updated_event = outbound_event_from_message(session_updated)
    assert isinstance(session_updated_event, SessionUpdatedEvent)
    assert session_updated_event.scope == "metadata"


def test_legacy_metadata_numbers_ignore_bool_values() -> None:
    turn_end = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="",
        metadata={"_turn_end": True, "latency_ms": True},
    )

    turn_end_event = outbound_event_from_message(turn_end)
    assert isinstance(turn_end_event, TurnEndEvent)
    assert turn_end_event.latency_ms is None


def test_legacy_retry_wait_and_streamed_flags_create_runtime_events() -> None:
    retry = OutboundMessage(
        channel="cli",
        chat_id="direct",
        content="waiting",
        metadata={"_retry_wait": True},
    )
    streamed = OutboundMessage(
        channel="cli",
        chat_id="direct",
        content="final answer",
        metadata={"_streamed": True},
    )

    retry_event = outbound_event_from_message(retry)
    assert isinstance(retry_event, RetryWaitEvent)
    assert retry_event.content == "waiting"
    assert isinstance(outbound_event_from_message(streamed), StreamedResponseEvent)


def test_replace_outbound_event_keeps_routing_metadata() -> None:
    msg = outbound_message_for_event(
        channel="websocket",
        chat_id="chat-1",
        event=StreamDeltaEvent(content="hello", stream_id="s1"),
        metadata={"message_id": "m1"},
    )

    updated = replace_outbound_event(
        msg,
        StreamEndEvent(stream_id="s1", resuming=True, merge_next=True),
        content="hello world",
    )

    assert updated.content == "hello world"
    assert updated.metadata == {"message_id": "m1"}
    assert isinstance(updated.event, StreamEndEvent)
    assert updated.event.stream_id == "s1"
    assert updated.event.resuming is True
    assert updated.event.merge_next is True


def test_streamed_response_event_keeps_final_content_outside_event_payload() -> None:
    msg = outbound_message_for_event(
        channel="cli",
        chat_id="direct",
        event=StreamedResponseEvent(),
        content="final answer",
    )

    assert msg.content == "final answer"
    assert isinstance(outbound_event_from_message(msg), StreamedResponseEvent)
