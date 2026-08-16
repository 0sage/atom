"""Email tokenization: minting, stability, canonicalization, round-trip."""

from __future__ import annotations

import json
import re
import stat

import pytest

from atom.privacy import tokens as tokens_module
from atom.privacy.tokens import (
    MASK_TYPES,
    SCHEMA_VERSION,
    TYPE_COMPANY,
    TYPE_EMAIL,
    TYPE_NAME,
    TYPE_SURNAME,
    TYPE_TEXT,
    UNKNOWN_TIMESTAMP,
    MaskError,
    TokenStore,
    canonical_email,
    detokenize,
    placeholder,
    tokenize,
    validate_mask,
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


class TestTimestampCache:
    """``_utc_now`` caches the second it describes.

    ``strftime`` measured 1.36us against 0.03us for the rest of ``_touch``
    combined — 94% of the work done on every resolution, formatting a string that
    cannot have changed within its own second. Egress calls it once per
    placeholder, so a reply carrying 200 of them paid for 200 identical stamps.
    Caching it made ``detokenize`` 2.8x faster.

    The cache is only safe because the stamp has second resolution by design (see
    ``_utc_now``'s docstring); these pin that it stays correct.
    """

    def test_shape_is_unchanged(self) -> None:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", tokens_module._utc_now())

    def test_agrees_with_an_uncached_computation(self, monkeypatch) -> None:
        from datetime import datetime, timezone

        monkeypatch.setattr(tokens_module, "_stamp_cache", (-1, ""))
        expected = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert tokens_module._utc_now() == expected

    def test_stable_within_one_second(self, monkeypatch) -> None:
        monkeypatch.setattr(tokens_module.time, "time", lambda: 1_800_000_000.25)
        monkeypatch.setattr(tokens_module, "_stamp_cache", (-1, ""))
        first = tokens_module._utc_now()
        monkeypatch.setattr(tokens_module.time, "time", lambda: 1_800_000_000.99)
        assert tokens_module._utc_now() == first

    def test_advances_when_the_second_does(self, monkeypatch) -> None:
        """A stale stamp would misreport when an entry was last relevant."""
        monkeypatch.setattr(tokens_module.time, "time", lambda: 1_800_000_000.0)
        monkeypatch.setattr(tokens_module, "_stamp_cache", (-1, ""))
        first = tokens_module._utc_now()
        monkeypatch.setattr(tokens_module.time, "time", lambda: 1_800_000_001.0)
        second = tokens_module._utc_now()
        assert second != first
        assert (first, second) == ("2027-01-15T08:00:00Z", "2027-01-15T08:00:01Z")

    def test_formats_once_per_second_not_once_per_call(self, monkeypatch) -> None:
        """The property the speedup rests on."""
        calls = {"n": 0}
        real = tokens_module.datetime

        class Counting(real):  # pyright: ignore[reportUntypedBaseClass]
            @classmethod
            def fromtimestamp(cls, *args, **kwargs):  # pyright: ignore[reportIncompatibleMethodOverride]
                calls["n"] += 1
                return real.fromtimestamp(*args, **kwargs)

        monkeypatch.setattr(tokens_module, "datetime", Counting)
        monkeypatch.setattr(tokens_module, "_stamp_cache", (-1, ""))
        monkeypatch.setattr(tokens_module.time, "time", lambda: 1_800_000_000.5)
        for _ in range(100):
            tokens_module._utc_now()
        assert calls["n"] == 1

    def test_resolution_still_records_a_usable_stamp(self, store: TokenStore) -> None:
        """End to end: the cache must not leave `last_used` empty or malformed."""
        token = tokenize("alex@example.com", store=store).strip()
        store.value_for(token)
        entry = json.loads(store.path.read_text())["entries"][token]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["last_used"])
        assert entry["hits"] == 1


class TestMintingIsBatched:
    """One save per ``tokenize`` call, not one per newly minted value.

    ``_save`` rewrites the whole map, so saving per value made minting *n* values
    quadratic in bytes written — 800 addresses wrote 65MB to produce a 160KB file,
    and filling the map took ~130s locally and ~400s on flash. Batching is what
    makes the cold path linear.

    Durability is unchanged in the ways that matter: the save still happens before
    the caller sees the text, and a direct ``token_for`` call outside a batch still
    writes immediately.
    """

    def _saves_during(self, monkeypatch, fn) -> int:
        calls = {"n": 0}
        real = TokenStore._save

        def counting(self: TokenStore) -> None:
            calls["n"] += 1
            real(self)

        monkeypatch.setattr(TokenStore, "_save", counting)
        fn()
        return calls["n"]

    def test_many_new_addresses_cost_one_save(self, store: TokenStore, monkeypatch) -> None:
        text = " ".join(f"user{i}@example.com" for i in range(50))
        saves = self._saves_during(monkeypatch, lambda: tokenize(text, store=store))
        assert saves == 1
        assert len(store) == 50

    def test_every_entry_is_on_disk_when_the_call_returns(self, store: TokenStore) -> None:
        """The batch must not defer past the point the caller can use the text."""
        text = " ".join(f"user{i}@example.com" for i in range(20))
        out = tokenize(text, store=store)

        persisted = json.loads(store.path.read_text())["entries"]
        assert len(persisted) == 20
        # Every placeholder in the returned text resolves from the file alone.
        reopened = TokenStore(path=store.path)
        for token in re.findall(r"«email:[0-9a-f]{8}»", out):
            assert reopened.value_for(token) is not None

    def test_text_without_new_addresses_writes_nothing(
        self, store: TokenStore, monkeypatch
    ) -> None:
        """A batch that mints nothing must not rewrite the map."""
        store.token_for(TYPE_EMAIL, "alex@example.com")
        saves = self._saves_during(
            monkeypatch, lambda: tokenize("mail alex@example.com", store=store)
        )
        assert saves == 0

    def test_direct_token_for_still_writes_immediately(self, store: TokenStore) -> None:
        """Outside a batch, minting is as durable as it always was."""
        token = store.token_for(TYPE_EMAIL, "alex@example.com")
        assert token is not None
        assert json.loads(store.path.read_text())["entries"][token]["hits"] == 0

    def test_nested_batches_save_once_at_the_outermost_exit(
        self, store: TokenStore, monkeypatch
    ) -> None:
        """An inner block must not declare the outer block's work durable."""

        def nested() -> None:
            with store.minting_batch():
                store.token_for(TYPE_EMAIL, "a@example.com")
                with store.minting_batch():
                    store.token_for(TYPE_EMAIL, "b@example.com")
                assert not store.path.exists(), "inner exit must not save"
                store.token_for(TYPE_EMAIL, "c@example.com")

        saves = self._saves_during(monkeypatch, nested)
        assert saves == 1
        assert len(TokenStore(path=store.path)) == 3

    def test_a_failed_batch_save_fails_the_call(self, store: TokenStore, monkeypatch) -> None:
        """Substitutions are already made, so a silent failure would strand them.

        Returning the text after a failed save would hand back placeholders with
        no entry behind them, making the addresses unrecoverable. Raising keeps
        the invariant that a placeholder in returned text is resolvable.
        """

        def boom(self: TokenStore) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(TokenStore, "_save", boom)
        with pytest.raises(OSError, match="disk full"):
            tokenize("alex@example.com", store=store)

    def test_first_resolution_still_writes_through_after_a_batch(
        self, store: TokenStore
    ) -> None:
        """The batch must not consume the first-use write-through of `_touch`.

        `_last_flush` starts at 0.0 so a process that resolves one token and exits
        still records it. Restarting that timer at batch exit would put the first
        resolution inside the throttle window instead.
        """
        token = tokenize("alex@example.com", store=store)
        store.value_for(token.strip())
        assert json.loads(store.path.read_text())["entries"][token.strip()]["hits"] == 1


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


class TestDeclaredMasks:
    """Values an operator declares with ``/mask``, rather than ones a pattern finds.

    There is no reliable regex for a person's name, so the command is the
    detection. The type is carried anyway because it is the only thing the model
    sees: ``«text:…» sent an invoice to «text:…»`` leaves it unable to tell a
    person from a company, so it cannot pick correct pronouns or grammar.
    """

    def test_a_declared_value_is_replaced(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        out = tokenize("Alexey called", store=store)
        assert "Alexey" not in out
        assert out.startswith("«name:")

    def test_the_type_appears_in_the_placeholder(self, store: TokenStore) -> None:
        store.add_mask(TYPE_SURNAME, "Petrov")
        assert "«surname:" in tokenize("Petrov called", store=store)

    def test_round_trip_restores_the_registered_spelling(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        assert detokenize(tokenize("Alexey called", store=store), store=store) == (
            "Alexey called"
        )

    def test_declaring_twice_is_idempotent(self, store: TokenStore) -> None:
        """Two tokens for one person is the error worth preventing."""
        first = store.add_mask(TYPE_NAME, "Alexey")
        assert store.add_mask(TYPE_NAME, "Alexey") == first
        assert len(store) == 1

    def test_case_variants_share_one_token(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        out = tokenize("ALEXEY and alexey and Alexey", store=store)
        assert len({part for part in out.split() if part.startswith("«")}) == 1

    def test_resolution_uses_the_registered_casing(self, store: TokenStore) -> None:
        """Deliberately lossy on display, the same trade `canonical_email` makes."""
        store.add_mask(TYPE_NAME, "Alexey")
        assert detokenize(tokenize("ALEXEY called", store=store), store=store) == (
            "Alexey called"
        )

    def test_masks_and_addresses_coexist(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        out = tokenize("Alexey at alexey@example.com", store=store)
        assert "«name:" in out
        assert "«email:" in out
        assert "alexey@example.com" not in out

    def test_a_mask_inside_an_address_does_not_split_it(self, store: TokenStore) -> None:
        """Addresses are substituted first, and `\\b` cannot match inside a token."""
        store.add_mask(TYPE_NAME, "alexey")
        out = tokenize("write to alexey@example.com", store=store)
        assert out.count("«") == 1
        assert "«email:" in out

    def test_removing_a_mask_stops_new_substitutions(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        store.remove_mask("Alexey")
        assert tokenize("Alexey called", store=store) == "Alexey called"

    def test_removing_a_mask_strands_tokens_already_written(self, store: TokenStore) -> None:
        """The honest outcome: the alternative is keeping plaintext after a delete."""
        token = tokenize("Alexey called", store=store)  # nothing declared yet
        store.add_mask(TYPE_NAME, "Alexey")
        token = tokenize("Alexey called", store=store).split()[0]
        store.remove_mask("Alexey")
        assert detokenize(token, store=store) == token

    def test_removing_an_unknown_value_is_not_an_error(self, store: TokenStore) -> None:
        assert store.remove_mask("never registered") is None

    def test_masks_listing_excludes_addresses(self, store: TokenStore) -> None:
        """Addresses are discovered, not declared, so they are not masks."""
        tokenize("alex@example.com", store=store)
        store.add_mask(TYPE_NAME, "Alexey")
        assert [entry[1] for entry in store.masks()] == [TYPE_NAME]

    def test_a_mask_added_mid_session_takes_effect(self, store: TokenStore) -> None:
        """The compiled alternation is cached, so this pins that it invalidates."""
        assert tokenize("Alexey called", store=store) == "Alexey called"
        store.add_mask(TYPE_NAME, "Alexey")
        assert "«name:" in tokenize("Alexey called", store=store)

    def test_no_masks_means_no_scanning_cost(self, store: TokenStore) -> None:
        assert store.mask_pattern() is None
        assert tokenize("ordinary text with no at sign", store=store) == (
            "ordinary text with no at sign"
        )


class TestMaskGateDoesNotWalkTheMap:
    """``mask_pattern`` runs once per :func:`tokenize`, so its cached path is hot.

    Deciding the cache was stale by building a signature over every entry made an
    operator who has never run ``/mask`` pay per entry: clean text cost 0.38us
    against an empty map and 141us against 10,000 — the marker gate was more
    expensive than the work it was guarding. A dirty flag replaced it, so these
    pin both halves: the flag is not set when nothing changed, and it *is* set at
    each of the three places the mask set moves.
    """

    def test_the_pattern_is_not_rebuilt_when_nothing_changed(
        self, store: TokenStore
    ) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        first = store.mask_pattern()
        assert first is not None
        assert store.mask_pattern() is first

    def test_minting_an_address_does_not_rebuild_the_pattern(
        self, store: TokenStore
    ) -> None:
        """Addresses are not masks, so they must not invalidate the alternation."""
        store.add_mask(TYPE_NAME, "Alexey")
        first = store.mask_pattern()
        store.token_for(TYPE_EMAIL, "alex@example.com")
        assert store.mask_pattern() is first

    def test_the_empty_answer_is_cached_too(self, store: TokenStore) -> None:
        """The no-masks case is the common one; it must not recheck per call."""
        assert store.mask_pattern() is None
        store.token_for(TYPE_EMAIL, "alex@example.com")
        assert store.mask_pattern() is None

    def test_adding_a_mask_rebuilds(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        first = store.mask_pattern()
        store.add_mask(TYPE_SURNAME, "Smirnov")
        second = store.mask_pattern()
        assert second is not None and second is not first
        assert second.search("Smirnov") is not None

    def test_removing_a_mask_rebuilds(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        store.add_mask(TYPE_SURNAME, "Smirnov")
        store.mask_pattern()
        store.remove_mask("Smirnov")
        rebuilt = store.mask_pattern()
        assert rebuilt is not None
        assert rebuilt.search("Smirnov") is None
        assert rebuilt.search("Alexey") is not None

    def test_removing_the_last_mask_rebuilds_to_none(self, store: TokenStore) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        assert store.mask_pattern() is not None
        store.remove_mask("Alexey")
        assert store.mask_pattern() is None

    def test_a_reload_rebuilds(self, store: TokenStore) -> None:
        """A pattern compiled before the map was read was built from another set."""
        store.add_mask(TYPE_NAME, "Alexey")
        reopened = TokenStore(path=store.path)
        pattern = reopened.mask_pattern()
        assert pattern is not None and pattern.search("Alexey") is not None

    def test_the_cached_path_does_not_scale_with_map_size(
        self, store: TokenStore
    ) -> None:
        """Pins the actual defect: no masks declared, cost independent of entries.

        Asserted as a ratio against the store's own small-map cost rather than an
        absolute, so it means the same on a Pi as on a dev machine. The old code
        was ~370x here, so a generous bound still fails it.
        """
        import time

        text = "the build succeeded after retrying the flaky test twice. " * 20

        def best_ns(iterations: int = 200) -> int:
            for _ in range(50):
                tokenize(text, store=store)
            samples = []
            for _ in range(iterations):
                start = time.perf_counter_ns()
                tokenize(text, store=store)
                samples.append(time.perf_counter_ns() - start)
            return min(samples)

        small = best_ns()
        with store.minting_batch():
            for i in range(5_000):
                store.token_for(TYPE_EMAIL, f"user{i}@example.com")
        large = best_ns()
        assert large < small * 4, f"{small}ns at 0 entries, {large}ns at 5000"


class TestMaskLookupIsIndexed:
    """``token_for_mask`` runs once per matched occurrence, so it must not scan.

    Scanning the map and casefolding every entry per hit made ingress degrade
    with registry size — measured at 2.1M hits/s for one mask against 113k for
    a thousand. These pin the index that replaced the scan, including the two
    places it has to be maintained by hand.
    """

    def test_a_mask_resolves_without_scanning_the_map(self, store: TokenStore) -> None:
        """The index is the only lookup path, so a hit proves it was populated."""
        store.add_mask(TYPE_NAME, "Alexey")
        for _ in range(200):
            store.token_for(TYPE_EMAIL, f"noise{_}@example.com")
        assert store.token_for_mask("alexey") is not None

    def test_the_index_survives_a_reload(self, store: TokenStore) -> None:
        """It is rebuilt in ``_load``, which a fresh store exercises from disk."""
        store.add_mask(TYPE_NAME, "Alexey")
        reopened = TokenStore(path=store.path)
        assert reopened.token_for_mask("ALEXEY") == store.token_for_mask("Alexey")

    def test_removing_a_mask_removes_it_from_the_index(self, store: TokenStore) -> None:
        """A stale index entry would resolve a value the operator deleted."""
        store.add_mask(TYPE_NAME, "Alexey")
        store.remove_mask("Alexey")
        assert store.token_for_mask("Alexey") is None

    def test_an_address_is_not_reachable_as_a_mask(self, store: TokenStore) -> None:
        """Addresses are discovered by pattern; the index holds declared values only."""
        tokenize("alex@example.com", store=store)
        assert store.token_for_mask("alex@example.com") is None

    def test_a_removed_mask_can_be_redeclared(self, store: TokenStore) -> None:
        """Mints a fresh token, which must land in the index rather than nothing."""
        first = store.add_mask(TYPE_NAME, "Alexey")
        store.remove_mask("Alexey")
        second = store.add_mask(TYPE_NAME, "Alexey")
        assert second is not None and second != first
        assert store.token_for_mask("alexey") == second


class TestMasksDoNotCorruptText:
    """A wrong substitution is worse than no substitution.

    The agent reasons over what comes back, so a mask that rewrites part of an
    ordinary word turns its input into nonsense. These are the shapes that
    caught it during development.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Alexeyevich arrived",  # mask is a prefix of a longer word
            "myAlexey",  # mask is a suffix
            "xAlexeyx",  # mask is interior
        ],
    )
    def test_a_mask_does_not_match_inside_a_longer_word(
        self, store: TokenStore, text: str
    ) -> None:
        store.add_mask(TYPE_NAME, "Alexey")
        assert tokenize(text, store=store) == text

    @pytest.mark.parametrize(
        "text",
        [
            "Alexey's laptop",
            "(Alexey)",
            '"Alexey" said',
            "Alexey, then",
            "Alexey-Petrov",
            "call Alexey.",
        ],
    )
    def test_a_mask_matches_around_punctuation(
        self, store: TokenStore, text: str
    ) -> None:
        """Boundaries must not be so strict that real occurrences are missed."""
        store.add_mask(TYPE_NAME, "Alexey")
        assert "Alexey" not in tokenize(text, store=store)

    def test_the_longer_of_two_overlapping_masks_wins(self, store: TokenStore) -> None:
        """Otherwise the surname survives in plaintext beside the masked name."""
        store.add_mask(TYPE_NAME, "Sage")
        store.add_mask(TYPE_SURNAME, "Sage Smith")
        out = tokenize("Sage Smith called", store=store)
        assert "Smith" not in out
        assert out.count("«") == 1

    def test_a_multi_word_mask_matches(self, store: TokenStore) -> None:
        store.add_mask(TYPE_COMPANY, "Acme Corp")
        assert "Acme" not in tokenize("works at Acme Corp today", store=store)

    def test_a_multi_word_mask_does_not_match_a_longer_name(
        self, store: TokenStore
    ) -> None:
        store.add_mask(TYPE_COMPANY, "Acme Corp")
        assert tokenize("Acme Corporation", store=store) == "Acme Corporation"

    def test_internal_whitespace_is_normalized(self, store: TokenStore) -> None:
        """"Acme   Corp" registered must still match "Acme Corp" as typed."""
        entity_type, value = validate_mask(TYPE_COMPANY, "Acme   Corp")
        store.add_mask(entity_type, value)
        assert "Acme" not in tokenize("at Acme Corp now", store=store)


class TestMaskValidation:
    """Refusals that exist because the alternative corrupts text.

    Messages name the rule and never quote the value: the reply travels back
    through the channel the value arrived on.
    """

    def test_a_short_value_is_refused(self) -> None:
        """Masking 'An' rewrites 'an update'; measured twice in one sentence."""
        with pytest.raises(MaskError, match="Too short"):
            validate_mask(TYPE_NAME, "An")

    def test_the_minimum_length_is_inclusive(self) -> None:
        assert validate_mask(TYPE_NAME, "Alex") == (TYPE_NAME, "Alex")

    def test_an_unknown_type_is_refused(self) -> None:
        with pytest.raises(MaskError, match="Unknown type"):
            validate_mask("nmae", "Alexey")

    def test_the_error_lists_the_known_types(self) -> None:
        with pytest.raises(MaskError) as excinfo:
            validate_mask("nmae", "Alexey")
        for known in MASK_TYPES:
            assert known in str(excinfo.value)

    def test_an_empty_value_is_refused(self) -> None:
        with pytest.raises(MaskError, match="Nothing to mask"):
            validate_mask(TYPE_NAME, "   ")

    def test_a_value_without_letters_or_digits_is_refused(self) -> None:
        """`\\b` has nothing to anchor against, so matches land unpredictably."""
        with pytest.raises(MaskError, match="letter or digit"):
            validate_mask(TYPE_TEXT, "---!!!")

    def test_guillemets_are_refused(self) -> None:
        """They delimit placeholders, so a mask containing one is ambiguous."""
        with pytest.raises(MaskError, match="Guillemets"):
            validate_mask(TYPE_NAME, "«name:aaaaaaaa»")

    def test_the_type_is_case_folded(self) -> None:
        assert validate_mask("NAME", "Alexey")[0] == TYPE_NAME

    def test_a_refusal_never_quotes_the_value(self) -> None:
        secret = "Alexey"
        with pytest.raises(MaskError) as excinfo:
            validate_mask("nmae", secret)
        assert secret not in str(excinfo.value)

    def test_every_declared_type_is_resolvable(self) -> None:
        """`_TOKEN_RE` accepts `[a-z]+`, so a type with an underscore would break."""
        for entity_type in MASK_TYPES:
            token = placeholder(entity_type, "a1b2c3d4")
            assert tokens_module._TOKEN_RE.fullmatch(token), entity_type
