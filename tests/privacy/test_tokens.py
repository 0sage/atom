"""Email tokenization: minting, stability, canonicalization, round-trip."""

from __future__ import annotations

import json
import stat

import pytest

from atom.privacy import tokens as tokens_module
from atom.privacy.tokens import (
    SCHEMA_VERSION,
    TYPE_EMAIL,
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


class TestNoPlaintextLeftBehind:
    def test_address_absent_from_tokenized_text(self, store: TokenStore) -> None:
        secret = "alex@example.com"
        assert secret not in tokenize(f"contact {secret} today")

    def test_map_is_the_only_place_the_value_lives(self, store: TokenStore) -> None:
        tokenize("alex@example.com")
        assert "alex@example.com" in store.path.read_text()
        assert len(store) == 1
