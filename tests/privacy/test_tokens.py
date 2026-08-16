"""Email tokenization: minting, stability, canonicalization, round-trip."""

from __future__ import annotations

import json
import stat

import pytest

from atom.privacy import tokens as tokens_module
from atom.privacy.tokens import (
    SCHEMA_VERSION,
    TYPE_EMAIL,
    UNKNOWN_TIMESTAMP,
    TokenStore,
    canonical_email,
    detokenize,
    tokenize,
)


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


class TestCanonicalEmail:
    def test_lowercases_domain_and_local(self) -> None:
        assert canonical_email("Alex@Example.COM") == "alex@example.com"

    def test_preserves_plus_tag(self) -> None:
        """Merging +tag addresses cannot be undone, so it is not attempted."""
        assert canonical_email("alex+work@example.com") == "alex+work@example.com"

    def test_preserves_dots(self) -> None:
        assert canonical_email("a.l.e.x@example.com") == "a.l.e.x@example.com"

    def test_value_without_at_sign(self) -> None:
        assert canonical_email("NoAtSign") == "noatsign"


class TestTokenize:
    def test_replaces_an_address(self, store: TokenStore) -> None:
        out = tokenize("mail alex@example.com now")
        assert "alex@example.com" not in out
        assert out.startswith("mail «email:")
        assert out.endswith("» now")

    def test_same_address_gets_one_token(self, store: TokenStore) -> None:
        out = tokenize("alex@example.com and alex@example.com")
        tokens = {part for part in out.split() if part.startswith("«")}
        assert len(tokens) == 1

    def test_case_variants_share_a_token(self, store: TokenStore) -> None:
        first = tokenize("alex@example.com")
        second = tokenize("ALEX@Example.COM")
        assert first == second

    def test_different_addresses_get_different_tokens(self, store: TokenStore) -> None:
        assert tokenize("a@example.com") != tokenize("b@example.com")

    def test_stable_across_calls(self, store: TokenStore) -> None:
        assert tokenize("alex@example.com") == tokenize("alex@example.com")

    def test_stable_across_store_instances(self, store: TokenStore) -> None:
        """A restart must not mint a second token for the same person."""
        first = tokenize("alex@example.com", store=store)
        reopened = TokenStore(path=store.path)
        assert tokenize("alex@example.com", store=reopened) == first

    def test_multiple_addresses_in_one_text(self, store: TokenStore) -> None:
        out = tokenize("from a@example.com to b@example.org")
        assert "@example.com" not in out
        assert "@example.org" not in out
        assert out.count("«email:") == 2

    def test_empty_text(self, store: TokenStore) -> None:
        assert tokenize("") == ""

    def test_text_without_addresses_is_untouched(self, store: TokenStore) -> None:
        text = "version 1.2.3, time 08:30, path a/b.c"
        assert tokenize(text) == text
        assert len(store) == 0

    @pytest.mark.parametrize(
        "text",
        [
            "no at sign here",
            "just @ alone",
            "@example.com",
            "trailing@",
            "a@b",  # no TLD
        ],
    )
    def test_non_addresses_are_not_replaced(self, store: TokenStore, text: str) -> None:
        assert tokenize(text) == text

    def test_address_in_url_is_replaced(self, store: TokenStore) -> None:
        """Better to over-tokenize a mailto than to leak the address."""
        assert "alex@example.com" not in tokenize("mailto:alex@example.com")

    def test_punctuation_after_address_is_preserved(self, store: TokenStore) -> None:
        out = tokenize("write to alex@example.com.")
        assert out.endswith("».") or out.endswith("»")
        assert "alex@example" not in out

    def test_subdomain_address(self, store: TokenStore) -> None:
        assert "@mail.example.co.uk" not in tokenize("x@mail.example.co.uk")


class TestDetokenize:
    def test_round_trip(self, store: TokenStore) -> None:
        assert detokenize(tokenize("mail alex@example.com")) == "mail alex@example.com"

    def test_round_trip_lowercases_original_casing(self, store: TokenStore) -> None:
        """Canonicalization is deliberate and lossy on display: one token per
        person beats preserving the sender's capitalization."""
        assert detokenize(tokenize("Alex@Example.COM")) == "alex@example.com"

    def test_unknown_token_is_left_alone(self, store: TokenStore) -> None:
        """A token from a lost map must not be replaced by an invented value."""
        text = "see «email:deadbeef» please"
        assert detokenize(text) == text

    def test_text_without_tokens_is_untouched(self, store: TokenStore) -> None:
        assert detokenize("nothing here") == "nothing here"

    def test_empty_text(self, store: TokenStore) -> None:
        assert detokenize("") == ""

    def test_resolves_multiple_tokens(self, store: TokenStore) -> None:
        out = tokenize("from a@example.com to b@example.org")
        assert detokenize(out) == "from a@example.com to b@example.org"

    def test_partial_marker_is_not_matched(self, store: TokenStore) -> None:
        assert detokenize("«email:short»") == "«email:short»"


class TestFileFormat:
    def test_version_and_type_are_persisted(self, store: TokenStore) -> None:
        tokenize("alex@example.com")
        data = json.loads(store.path.read_text())
        assert data["version"] == SCHEMA_VERSION
        entry = next(iter(data["entries"].values()))
        assert entry["type"] == TYPE_EMAIL
        assert entry["value"] == "alex@example.com"

    def test_keyed_by_token(self, store: TokenStore) -> None:
        """Detokenization is the direction correctness depends on."""
        tokenize("alex@example.com")
        data = json.loads(store.path.read_text())
        assert all(key.startswith("«email:") for key in data["entries"])

    def test_file_is_owner_only(self, store: TokenStore) -> None:
        tokenize("alex@example.com")
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    def test_missing_file_is_empty(self, store: TokenStore) -> None:
        assert len(store) == 0
        assert store.value_for("«email:deadbeef»") is None

    def test_malformed_entries_are_dropped_not_fatal(self, store: TokenStore) -> None:
        store.path.write_text(
            json.dumps({
                "version": 1,
                "entries": {
                    "«email:aaaaaaaa»": {"type": "email", "value": "ok@example.com"},
                    "«email:bbbbbbbb»": {"type": "email"},
                    "«email:cccccccc»": "not-an-object",
                },
            })
        )
        assert store.value_for("«email:aaaaaaaa»") == "ok@example.com"
        assert store.value_for("«email:bbbbbbbb»") is None

    def test_corrupt_file_disables_minting_without_overwriting(
        self, store: TokenStore,
    ) -> None:
        """Rewriting a recoverable file would strand every token in history."""
        store.path.write_text("{ this is not json")
        original = store.path.read_text()
        assert tokenize("alex@example.com") == "alex@example.com"
        assert store.path.read_text() == original

    def test_unknown_extra_fields_survive_a_read(self, store: TokenStore) -> None:
        store.path.write_text(
            json.dumps({
                "version": 1,
                "entries": {
                    "«email:aaaaaaaa»": {
                        "type": "email", "value": "ok@example.com", "future": "x",
                    },
                },
            })
        )
        assert store.value_for("«email:aaaaaaaa»") == "ok@example.com"


class TestUsageMetadata:
    """``created``/``last_used``/``hits`` exist to make the map prunable.

    At ``MAX_ENTRIES`` an operator has to choose what to drop, and without a
    last-used stamp the only options are deleting the file — which strands every
    token in saved history — or keeping it forever. Nothing evicts automatically:
    dropping an entry makes its placeholder unresolvable wherever it was already
    written, so the choice stays with a person.
    """

    def test_mint_records_creation(self, store: TokenStore) -> None:
        token = tokenize("alex@example.com")
        entry = json.loads(store.path.read_text())["entries"][token]
        assert entry["created"].endswith("Z")
        assert entry["last_used"] == entry["created"]
        assert entry["hits"] == 0, "minting is not a use"

    def test_resolving_counts_as_a_use(self, store: TokenStore) -> None:
        token = tokenize("alex@example.com")
        for _ in range(3):
            detokenize(token)
        store.flush()
        entry = json.loads(store.path.read_text())["entries"][token]
        assert entry["hits"] == 3

    def test_unresolved_token_does_not_count(self, store: TokenStore) -> None:
        token = tokenize("alex@example.com")
        detokenize("«email:deadbeef»")
        store.flush()
        entry = json.loads(store.path.read_text())["entries"][token]
        assert entry["hits"] == 0

    def test_first_use_is_written_through(self, store: TokenStore) -> None:
        """The first resolution flushes rather than waiting out the interval.

        Deliberate: a process that resolves a token once and exits would
        otherwise persist nothing unless something called ``flush``, and the
        write is cheap precisely because it happens once.
        """
        token = tokenize("alex@example.com")
        detokenize(token)
        assert json.loads(store.path.read_text())["entries"][token]["hits"] == 1

    def test_burst_after_the_first_is_deferred(self, store: TokenStore) -> None:
        """Detokenization runs per stream delta; writing through every one would
        fsync the plaintext map hundreds of times for a single reply."""
        token = tokenize("alex@example.com")
        detokenize(token)  # opens the throttle window
        before = store.path.stat().st_mtime_ns
        for _ in range(50):
            detokenize(token)
        assert store.path.stat().st_mtime_ns == before, "no write inside the window"
        assert store._load()[token]["hits"] == 51, "but every use is counted"

    def test_flush_persists_counters_held_in_memory(self, store: TokenStore) -> None:
        token = tokenize("alex@example.com")
        detokenize(token)
        for _ in range(4):
            detokenize(token)
        assert json.loads(store.path.read_text())["entries"][token]["hits"] == 1
        store.flush()
        assert json.loads(store.path.read_text())["entries"][token]["hits"] == 5

    def test_flush_is_a_no_op_without_pending_writes(self, store: TokenStore) -> None:
        tokenize("alex@example.com")
        before = store.path.stat().st_mtime_ns
        store.flush()
        assert store.path.stat().st_mtime_ns == before


class TestUsageMetadataBackCompat:
    """A v1 file predating these fields must stay readable, unbumped.

    The fields are metadata about a mapping rather than part of one, so an old
    file is not a breaking change: the parser backfills what it cannot know.
    """

    def _write_legacy(self, store: TokenStore) -> str:
        token = "«email:aaaaaaaa»"
        store.path.write_text(
            json.dumps({
                "version": 1,
                "entries": {token: {"type": "email", "value": "old@example.com"}},
            })
        )
        return token

    def test_legacy_entry_still_resolves(self, store: TokenStore) -> None:
        token = self._write_legacy(store)
        assert store.value_for(token) == "old@example.com"

    def test_missing_created_is_marked_unknown_not_now(self, store: TokenStore) -> None:
        """Backfilling the load time would make every old entry look fresh and
        destroy the signal the field exists to carry."""
        token = self._write_legacy(store)
        store.value_for(token)
        assert store._load()[token]["created"] == UNKNOWN_TIMESTAMP

    def test_legacy_entry_starts_counting_from_zero(self, store: TokenStore) -> None:
        token = self._write_legacy(store)
        store.value_for(token)
        store.flush()
        assert json.loads(store.path.read_text())["entries"][token]["hits"] == 1

    def test_bool_is_not_accepted_as_a_count(self, store: TokenStore) -> None:
        """bool subclasses int, so a hand-edited `true` would otherwise count."""
        token = "«email:aaaaaaaa»"
        store.path.write_text(
            json.dumps({
                "version": 1,
                "entries": {
                    token: {
                        "type": "email", "value": "x@example.com", "hits": True,
                    },
                },
            })
        )
        assert store._load()[token]["hits"] == 0

    def test_entry_built_in_process_does_not_break_egress(
        self, store: TokenStore,
    ) -> None:
        """_touch runs on the path that resolves placeholders for the user, so a
        missing counter must not turn a reply into a KeyError."""
        token = "«email:bbbbbbbb»"
        store._entries = {token: {"type": "email", "value": "y@example.com"}}  # pyright: ignore[reportArgumentType] — deliberately partial
        store._by_value = {("email", "y@example.com"): token}
        assert store.value_for(token) == "y@example.com"
        assert store._load()[token]["hits"] == 1


class TestNoPlaintextLeftBehind:
    def test_address_absent_from_tokenized_text(self, store: TokenStore) -> None:
        secret = "alex@example.com"
        assert secret not in tokenize(f"contact {secret} today")

    def test_map_is_the_only_place_the_value_lives(self, store: TokenStore) -> None:
        tokenize("alex@example.com")
        assert "alex@example.com" in store.path.read_text()
        assert len(store) == 1


class TestExplicitStoreIsAlwaysHonoured:
    """An explicitly passed store must never be swapped for the default one.

    ``__len__`` makes an empty store falsy, so the idiomatic
    ``store or DEFAULT_TOKEN_STORE`` discarded the caller's store for exactly as
    long as it was empty — its whole first-use window — and wrote to the real
    map at ``~/.atom/private/tokens.json`` instead. Found by a benchmark whose
    isolated stores stayed empty while the user's map filled to ``MAX_ENTRIES``.
    """

    def test_empty_store_is_truthy(self, tmp_path) -> None:
        assert bool(TokenStore(path=tmp_path / "t.json")) is True

    def test_tokenize_writes_to_the_given_empty_store(self, tmp_path, monkeypatch) -> None:
        default = TokenStore(path=tmp_path / "default.json")
        monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", default)
        explicit = TokenStore(path=tmp_path / "explicit.json")

        out = tokenize("mail alex@example.com", store=explicit)

        assert "«email:" in out
        assert len(explicit) == 1
        assert len(default) == 0
        assert not default.path.exists()

    def test_detokenize_reads_from_the_given_empty_store(self, tmp_path, monkeypatch) -> None:
        """A default map holding the same token must not answer for another store."""
        default = TokenStore(path=tmp_path / "default.json")
        monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", default)
        explicit = TokenStore(path=tmp_path / "explicit.json")
        token = tokenize("alex@example.com", store=explicit).strip()
        default_token = tokenize("someone.else@example.org", store=default)

        assert detokenize(token, store=explicit) == "alex@example.com"
        # The default store is non-empty now, so a truthiness fallback would look
        # correct here; what it must not do is answer for `explicit`.
        assert detokenize(default_token, store=explicit) == default_token

    def test_first_and_later_calls_use_the_same_store(self, tmp_path, monkeypatch) -> None:
        """The bug only showed on the first call, while the store was empty."""
        default = TokenStore(path=tmp_path / "default.json")
        monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", default)
        explicit = TokenStore(path=tmp_path / "explicit.json")

        tokenize("first@example.com", store=explicit)
        tokenize("second@example.com", store=explicit)

        assert len(explicit) == 2
        assert len(default) == 0


class TestPathologicalInputStaysLinear:
    """Bulk tool output must not be able to stall the engine.

    ``_EMAIL_RE`` carries a lookbehind so a long run of local-part-legal
    characters is tried from one offset instead of every offset. Without it the
    cost is quadratic in the length of the run, and tool output reaches that
    easily: base64 blobs and hex dumps are made of local-part-legal characters,
    and one ``@`` anywhere past such a run defeats the cheap ``"@" not in text``
    exit. Measured before the guard: ~340ms for 24KB, ~1s for 40KB.

    Timing is asserted only as a ceiling generous enough for the slowest rung in
    ``scripts/bench_privacy.py``'s ladder (flash storage on ARM). The scaling
    assertion is the real test: quadratic growth fails it on any hardware.
    """

    #: Local-part-legal, so every offset in a run of these is a candidate start.
    BLOB = "QUJDREVG+/=."

    def test_long_run_without_a_domain_is_not_quadratic(self, store: TokenStore) -> None:
        import time

        def cost_ms(chars: int) -> float:
            text = self.BLOB * (chars // len(self.BLOB)) + "@ "
            start = time.perf_counter_ns()
            tokenize(text, store=store)
            return (time.perf_counter_ns() - start) / 1e6

        cost_ms(2_000)  # discard: first call pays for lazily compiled internals
        small = cost_ms(5_000)
        large = cost_ms(20_000)
        # Quadratic would be ~16x for 4x the input. Linear is ~4x. The bound sits
        # between them with room for timer noise on a loaded CI host.
        assert large < max(small, 0.05) * 10

    def test_pathological_shapes_finish_promptly(self, store: TokenStore) -> None:
        import time

        shapes = {
            "blob_then_at": self.BLOB * 2_000 + "@ ",
            "long_local_no_domain": "a" * 20_000 + "@ ",
            "hex_dump_then_at": "deadbeef" * 3_000 + "@ ",
            "at_without_domain_repeated": "user@ " * 4_000,
            "run_with_at_every_100": ("a" * 100 + "@") * 200,
        }
        for name, text in shapes.items():
            start = time.perf_counter_ns()
            tokenize(text, store=store)
            elapsed_ms = (time.perf_counter_ns() - start) / 1e6
            assert elapsed_ms < 100, f"{name} took {elapsed_ms:.1f}ms"

    def test_no_address_was_stored_for_any_of_them(self, store: TokenStore) -> None:
        """The shapes above are near-misses: none is an address worth a token."""
        tokenize(self.BLOB * 100 + "@ ", store=store)
        tokenize("a" * 1_000 + "@ ", store=store)
        assert len(store) == 0

    @pytest.mark.parametrize(
        "text",
        [
            "mailto:alex@example.com",
            "<alex@example.com>",
            '"alex@example.com"',
            "(alex@example.com)",
            "alex@example.com",
            "to:alex@example.com,bob@example.org",
            "[alex@example.com]",
            "send\talex@example.com\tnow",
        ],
    )
    def test_the_guard_does_not_hide_a_real_address(
        self, store: TokenStore, text: str
    ) -> None:
        """Every real address is preceded by something not in a local part.

        The lookbehind is a performance guard, so it must not narrow what
        matches. These are the delimiters an address actually arrives behind.
        """
        assert "@example." not in tokenize(text, store=store)
