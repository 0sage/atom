"""Tool output is the larger ingress path, and it has its own hook.

``exec`` running a query, ``web_fetch`` reading an endpoint, and every MCP
wrapper all funnel through ``normalize_tool_result``, so one hook there covers
them. These tests pin that boundary and the map's size cap, which exists because
a single bulk result can carry thousands of addresses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atom.agent.context_governance import ContextGovernanceConfig, ContextGovernor
from atom.agent.tools.registry import ToolRegistry
from atom.privacy import tokens as tokens_module
from atom.privacy.hooks import tokenize_injected_text, tokenize_tool_result
from atom.privacy.tokens import (
    CAPPED_PLACEHOLDER,
    MAX_ENTRIES,
    TokenStore,
    detokenize,
)

EMAIL = "alex@corp.example"


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


class TestToolResultShapes:
    """A result is a string, a content-block list, or a dict, per tool."""

    def test_plain_string(self, store: TokenStore) -> None:
        out = tokenize_tool_result(f"contact: {EMAIL}")
        assert EMAIL not in out
        assert "«email:" in out

    def test_content_block_list(self, store: TokenStore) -> None:
        out = tokenize_tool_result([{"type": "text", "text": EMAIL}])
        assert out[0]["text"] != EMAIL
        assert "«email:" in out[0]["text"]

    def test_nested_dict(self, store: TokenStore) -> None:
        out = tokenize_tool_result({"rows": [{"email": EMAIL}]})
        assert EMAIL not in str(out)

    def test_non_text_leaves_are_preserved(self, store: TokenStore) -> None:
        out = tokenize_tool_result({"count": 42, "ok": True, "missing": None})
        assert out == {"count": 42, "ok": True, "missing": None}

    def test_bulk_output_shares_tokens_per_person(self, store: TokenStore) -> None:
        """The same address twice in one result is one map entry."""
        out = tokenize_tool_result(f"{EMAIL}\n{EMAIL}\nother@corp.example")
        assert len(store) == 2
        first = out.splitlines()[0]
        assert out.splitlines()[1] == first

    def test_result_without_addresses_is_untouched(self, store: TokenStore) -> None:
        payload = {"rows": ["no contact data", 7]}
        assert tokenize_tool_result(payload) == payload
        assert len(store) == 0

    def test_round_trips_for_the_user(self, store: TokenStore) -> None:
        out = tokenize_tool_result(f"contact: {EMAIL}")
        assert detokenize(out) == f"contact: {EMAIL}"


class TestNormalizeToolResult:
    """The hook must sit inside normalize_tool_result, before the offload path."""

    def _config(self, *, enabled: bool, workspace: Path | None = None):
        return ContextGovernanceConfig(
            provider=None,  # pyright: ignore[reportArgumentType] — unused here
            model="m",
            tools=ToolRegistry(),
            workspace=workspace,
            session_key="cli:1",
            max_tool_result_chars=100_000,
            tokenize_tool_results=enabled,
        )

    def test_exec_output_is_tokenized(self, store: TokenStore) -> None:
        result = ContextGovernor.normalize_tool_result(
            self._config(enabled=True), "call-1", "exec", f"found {EMAIL}",
        )
        assert EMAIL not in result
        assert "«email:" in result

    def test_mcp_output_is_tokenized(self, store: TokenStore) -> None:
        """MCP tools are ordinary Tools, so they need no separate hook."""
        result = ContextGovernor.normalize_tool_result(
            self._config(enabled=True), "call-2", "mcp_mail_list_messages",
            f'{{"from": "{EMAIL}"}}',
        )
        assert EMAIL not in result

    def test_read_file_is_tokenized_despite_offload_exemption(
        self, store: TokenStore,
    ) -> None:
        """read_file returns early for offload, so the hook must run before that."""
        result = ContextGovernor.normalize_tool_result(
            self._config(enabled=True), "call-3", "read_file", f"owner: {EMAIL}",
        )
        assert EMAIL not in result

    def test_disabled_leaves_output_alone(self, store: TokenStore) -> None:
        text = f"found {EMAIL}"
        result = ContextGovernor.normalize_tool_result(
            self._config(enabled=False), "call-4", "exec", text,
        )
        assert result == text
        assert len(store) == 0

    def test_empty_result_marker_is_not_broken(self, store: TokenStore) -> None:
        result = ContextGovernor.normalize_tool_result(
            self._config(enabled=True), "call-5", "exec", "",
        )
        assert isinstance(result, str)
        assert result != ""


class TestInjectedText:
    """Subagent results arrive on the system channel, past the user-text hook."""

    def test_tokenizes_when_enabled(self, store: TokenStore) -> None:
        assert EMAIL not in tokenize_injected_text(f"done: {EMAIL}", enabled=True)

    def test_passthrough_when_disabled(self, store: TokenStore) -> None:
        text = f"done: {EMAIL}"
        assert tokenize_injected_text(text, enabled=False) == text

    def test_already_tokenized_text_is_a_no_op(self, store: TokenStore) -> None:
        """The common case: a subagent's own inputs were already tokenized."""
        once = tokenize_injected_text(f"done: {EMAIL}", enabled=True)
        assert tokenize_injected_text(once, enabled=True) == once
        assert len(store) == 1

    def test_empty_text(self, store: TokenStore) -> None:
        assert tokenize_injected_text("", enabled=True) == ""


class TestMapFileIsSelfNeutralizing:
    """Reading tokens.json through a tool cannot disclose what it maps.

    The map is the one file holding plaintext addresses, and the agent can reach
    it whenever workspace restriction is off. That is survivable only because of
    a property worth pinning: an entry's ``value`` is the plaintext keyed by its
    own token, so the tool-result hook rewrites it into that same token. The file
    describes its own contents in placeholder terms.
    """

    def test_reading_the_map_yields_tokens_not_addresses(
        self, store: TokenStore, tmp_path,
    ) -> None:
        token = tokenize_tool_result(EMAIL)
        raw = (tmp_path / "tokens.json").read_text()
        assert EMAIL in raw, "precondition: the map holds plaintext on disk"

        seen = tokenize_tool_result(raw)
        assert EMAIL not in seen
        assert token in seen

    def test_disabled_leaves_the_map_readable(
        self, store: TokenStore, tmp_path,
    ) -> None:
        """The property belongs to the hook, not the file — turning it off loses it.

        A map minted while tokenization was on stays plaintext on disk after it is
        switched off, and nothing rewrites it then. Recorded so the guarantee is
        not read as stronger than it is.
        """
        tokenize_tool_result(EMAIL)
        raw = (tmp_path / "tokens.json").read_text()
        assert EMAIL in tokenize_injected_text(raw, enabled=False)


class TestSizeCap:
    """Bulk tool output is why this exists: the map is append-only."""

    def _fill(self, store: TokenStore, count: int) -> None:
        store._entries = {
            f"«email:{i:08x}»": {"type": "email", "value": f"u{i}@filler.example"}
            for i in range(count)
        }
        store._by_value = {
            ("email", entry["value"]): token
            for token, entry in store._entries.items()
        }

    def test_new_values_are_capped_not_leaked(self, store: TokenStore) -> None:
        self._fill(store, MAX_ENTRIES)
        out = tokenize_tool_result(f"new: {EMAIL}")
        assert EMAIL not in out, "a size limit must never become a disclosure"
        assert CAPPED_PLACEHOLDER in out

    def test_capped_marker_does_not_resolve(self, store: TokenStore) -> None:
        self._fill(store, MAX_ENTRIES)
        out = tokenize_tool_result(f"new: {EMAIL}")
        assert detokenize(out) == out

    def test_known_values_still_resolve_when_full(self, store: TokenStore) -> None:
        """Reaching the cap must not break addresses already in the map."""
        known = tokenize_tool_result(f"first: {EMAIL}")
        self._fill_preserving(store, MAX_ENTRIES)
        assert detokenize(known) == f"first: {EMAIL}"

    def _fill_preserving(self, store: TokenStore, count: int) -> None:
        existing = dict(store._entries or {})
        self._fill(store, count)
        store._entries.update(existing)
        store._by_value.update(
            {("email", e["value"]): t for t, e in existing.items()}
        )

    def test_under_the_cap_still_mints(self, store: TokenStore) -> None:
        self._fill(store, MAX_ENTRIES - 1)
        out = tokenize_tool_result(f"new: {EMAIL}")
        assert CAPPED_PLACEHOLDER not in out
        assert "«email:" in out

    def test_cap_is_not_written_to_disk_as_an_entry(self, store: TokenStore) -> None:
        self._fill(store, MAX_ENTRIES)
        tokenize_tool_result(f"new: {EMAIL}")
        assert CAPPED_PLACEHOLDER not in (store._entries or {})
