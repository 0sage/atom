"""Tests for the turn persistence boundary helpers."""

from __future__ import annotations

from atom.session.turn_continuation import (
    SKIP_USER_PERSIST_META,
    _save_skip_for_turn,
    should_persist_user_message,
)


def test_persists_user_message_by_default():
    assert should_persist_user_message(None) is True
    assert should_persist_user_message({}) is True
    assert should_persist_user_message({"message_id": "m1"}) is True


def test_skip_user_persist_metadata_suppresses_persistence():
    assert should_persist_user_message({SKIP_USER_PERSIST_META: True}) is False
    # Only an exact True opts out; a truthy value must not silently skip persistence.
    assert should_persist_user_message({SKIP_USER_PERSIST_META: "yes"}) is True


def test_save_skip_matches_prefix_when_current_message_merged():
    skip = _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=2,  # [system, merged user]
        history_count=1,
        input_persisted_early=True,
    )
    assert skip == 2


def test_save_skip_unchanged_for_standalone_current_message():
    # [system, history user, current user] with the current user already saved.
    assert _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=3,
        history_count=1,
        input_persisted_early=True,
    ) == 3
    assert _save_skip_for_turn(
        message_metadata=None,
        initial_message_count=3,
        history_count=1,
        input_persisted_early=False,
    ) == 2


def test_save_skip_keeps_whole_prefix_when_user_input_not_persisted():
    """A non-persisted user message must not shift the append boundary."""
    assert _save_skip_for_turn(
        message_metadata={SKIP_USER_PERSIST_META: True},
        initial_message_count=3,
        history_count=1,
        input_persisted_early=False,
    ) == 3
