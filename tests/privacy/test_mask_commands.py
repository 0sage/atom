"""``/mask`` command replies, and what they must never contain.

The reply travels back through the same chat the value arrived on, so a reply that
quotes the value undoes the command. These pin that, plus the routing property
that keeps the command text out of session history.
"""

from __future__ import annotations

import pytest

from atom.privacy import tokens as tokens_module
from atom.privacy.mask_commands import USAGE, handle_mask_command
from atom.privacy.tokens import MASK_TYPES, TYPE_NAME, TokenStore, tokenize


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    replacement = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_module, "DEFAULT_TOKEN_STORE", replacement)
    return replacement


class TestAdding:
    def test_a_value_is_masked_after_the_command(self, store: TokenStore) -> None:
        handle_mask_command("name Alexey", store)
        assert "Alexey" not in tokenize("Alexey called", store=store)

    def test_the_reply_names_the_type(self, store: TokenStore) -> None:
        assert "name" in handle_mask_command("name Alexey", store).text

    def test_a_multi_word_value_keeps_its_spaces(self, store: TokenStore) -> None:
        handle_mask_command("company Acme Corp", store)
        assert "Acme" not in tokenize("at Acme Corp", store=store)

    def test_running_it_twice_does_not_mint_a_second_token(
        self, store: TokenStore
    ) -> None:
        handle_mask_command("name Alexey", store)
        handle_mask_command("name Alexey", store)
        assert len(store) == 1

    def test_the_type_may_be_capitalized(self, store: TokenStore) -> None:
        handle_mask_command("NAME Alexey", store)
        assert "«name:" in tokenize("Alexey", store=store)

    def test_a_bare_type_shows_usage_without_deleting(self, store: TokenStore) -> None:
        """Nothing was typed to delete, and the user is mid-command."""
        reply = handle_mask_command("name", store)
        assert reply.carried_value is False
        assert "Usage" in reply.text
        assert len(store) == 0


class TestANewTypeIsFlagged:
    """An open namespace means a typo now succeeds, so the reply has to say so.

    ``/mask nmae Alexey`` was refused with the valid types listed; it now mints a
    type nobody meant. The note is the whole mitigation, and it must appear only
    for types outside :data:`MASK_TYPES` — on every add, it would be noise that
    gets ignored precisely when it matters.
    """

    def test_an_undocumented_type_is_stored_and_works(self, store: TokenStore) -> None:
        iban = "GB82WEST12345698765432"
        handle_mask_command(f"iban {iban}", store)
        assert "«iban:" in tokenize(f"pay {iban}", store=store)

    def test_the_reply_flags_a_new_type(self, store: TokenStore) -> None:
        text = handle_mask_command("iban GB82WEST12345698765432", store).text
        assert "new type" in text
        assert "iban" in text

    def test_the_reply_says_how_to_undo(self, store: TokenStore) -> None:
        """The note is useless if it names the problem without the remedy."""
        text = handle_mask_command("nmae Someone", store).text
        assert "/mask del" in text

    def test_a_documented_type_is_not_flagged(self, store: TokenStore) -> None:
        assert "new type" not in handle_mask_command("name Alexey", store).text

    def test_the_note_never_quotes_the_value(self, store: TokenStore) -> None:
        iban = "GB82WEST12345698765432"
        assert iban not in handle_mask_command(f"iban {iban}", store).text

    def test_the_listing_shows_a_typo_type(self, store: TokenStore) -> None:
        """The second place a typo becomes visible, without printing values."""
        handle_mask_command("nmae Someone", store)
        assert "nmae" in handle_mask_command("", store).text

    def test_usage_documents_the_open_namespace(self) -> None:
        assert "lowercase word" in USAGE


class TestListing:
    def test_empty_listing_shows_usage(self, store: TokenStore) -> None:
        assert "Nothing is masked" in handle_mask_command("", store).text

    def test_listing_shows_types_and_lengths(self, store: TokenStore) -> None:
        handle_mask_command("name Alexey", store)
        text = handle_mask_command("", store).text
        assert "name (6 chars)" in text

    def test_listing_never_prints_a_value(self, store: TokenStore) -> None:
        """Otherwise one command puts every masked name back into the chat."""
        handle_mask_command("name Alexey", store)
        handle_mask_command("company Acme Corp", store)
        text = handle_mask_command("list", store).text
        assert "Alexey" not in text
        assert "Acme" not in text

    def test_listing_does_not_ask_for_a_delete(self, store: TokenStore) -> None:
        """No value was typed, so the user's message is harmless."""
        handle_mask_command("name Alexey", store)
        assert handle_mask_command("", store).carried_value is False

    def test_listing_excludes_discovered_addresses(self, store: TokenStore) -> None:
        tokenize("alex@example.com", store=store)
        assert "Nothing is masked" in handle_mask_command("", store).text


class TestRemoving:
    @pytest.mark.parametrize("verb", ["del", "delete", "rm", "remove", "unmask"])
    def test_every_spelling_removes(self, store: TokenStore, verb: str) -> None:
        handle_mask_command("name Alexey", store)
        handle_mask_command(f"{verb} Alexey", store)
        assert tokenize("Alexey called", store=store) == "Alexey called"

    def test_the_reply_says_old_placeholders_stop_resolving(
        self, store: TokenStore
    ) -> None:
        """The consequence is real, so the reply states it rather than implying success."""
        handle_mask_command("name Alexey", store)
        assert "not resolve" in handle_mask_command("del Alexey", store).text

    def test_removing_an_unmasked_value_is_not_an_error(self, store: TokenStore) -> None:
        assert "not masked" in handle_mask_command("del Nobody", store).text

    def test_a_bare_del_shows_usage(self, store: TokenStore) -> None:
        assert "Usage" in handle_mask_command("del", store).text


class TestRefusals:
    def test_a_malformed_type_lists_the_documented_ones(self, store: TokenStore) -> None:
        text = handle_mask_command("bank_account Alexey", store).text
        assert "single lowercase word" in text
        for known in MASK_TYPES:
            assert known in text

    def test_a_short_value_is_refused_with_the_reason(self, store: TokenStore) -> None:
        text = handle_mask_command("name An", store).text
        assert "Too short" in text
        assert len(store) == 0

    def test_a_refusal_still_deletes_the_message(self, store: TokenStore) -> None:
        """A value typed beside a bad type was still typed into the chat."""
        assert handle_mask_command("bank_account Alexey", store).carried_value is True

    def test_a_refusal_never_quotes_the_value(self, store: TokenStore) -> None:
        assert "Alexey" not in handle_mask_command("bank_account Alexey", store).text

    def test_help_shows_usage_without_deleting(self, store: TokenStore) -> None:
        reply = handle_mask_command("help", store)
        assert reply.text == USAGE
        assert reply.carried_value is False

    def test_usage_documents_that_the_type_is_required(self) -> None:
        assert "type is required" in USAGE


class TestRouting:
    """The command must dispatch where nothing is persisted.

    ``tokenize_user_text`` skips anything starting with ``/``, so a slash command
    is not tokenized — which is correct for ``/mask`` (the value must reach the
    store byte-for-byte) and is exactly why the priority tier matters instead.
    """

    def test_registered_on_the_priority_tier(self) -> None:
        from atom.command.builtin import register_builtin_commands
        from atom.command.router import CommandRouter

        router = CommandRouter()
        register_builtin_commands(router)
        for text in ("/mask", "/masks", "/mask name Alexey", "/masks del Alexey"):
            assert router.is_priority(text), text
            # Priority commands are handled on their own path, so the ordinary
            # dispatcher must decline them — that is what keeps the arguments out
            # of session history.
            assert not router.is_dispatchable_command(text), text

    def test_the_command_text_is_not_tokenized(self, store: TokenStore) -> None:
        from atom.privacy.hooks import tokenize_user_text

        handle_mask_command("name Alexey", store)
        text = "/mask name Alexey"
        assert tokenize_user_text(text, enabled=True) == text

    def test_an_ordinary_message_is_tokenized_afterwards(self, store: TokenStore) -> None:
        from atom.privacy.hooks import tokenize_user_text

        handle_mask_command("name Alexey", store)
        assert "Alexey" not in tokenize_user_text("tell Alexey now", enabled=True)


class TestToolOutputIsCovered:
    """The larger exposure: a mask must apply to what tools return, not just chat."""

    def test_a_masked_value_in_a_tool_result_is_replaced(self, store: TokenStore) -> None:
        from atom.privacy.hooks import tokenize_tool_result

        handle_mask_command("name Alexey", store)
        result = {"rows": [{"owner": "Alexey", "note": "ok"}]}
        masked = tokenize_tool_result(result)
        assert "Alexey" not in str(masked)
        assert "«name:" in str(masked)

    def test_the_type_survives_into_tool_output(self, store: TokenStore) -> None:
        from atom.privacy.hooks import tokenize_tool_result

        handle_mask_command("company Acme Corp", store)
        assert "«company:" in str(tokenize_tool_result(["works at Acme Corp"]))

    def test_masks_are_shown_back_to_the_user(self, store: TokenStore) -> None:
        from atom.privacy.tokens import detokenize

        handle_mask_command(f"{TYPE_NAME} Alexey", store)
        masked = tokenize("Alexey called", store=store)
        assert detokenize(masked, store=store) == "Alexey called"
