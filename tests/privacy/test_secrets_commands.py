"""``/secrets`` reply text: never echoes a value, including on error."""

from __future__ import annotations

import pytest

from atom.privacy.commands import handle_secrets_command
from atom.privacy.store import SecretStore

SECRET = "ghp_s3cr3t_value_9999"


@pytest.fixture
def store(tmp_path) -> SecretStore:
    return SecretStore(path=tmp_path / "secrets.env")


def run(args: str, store: SecretStore) -> str:
    return handle_secrets_command(args, store=store)


class TestList:
    def test_empty_shows_usage(self, store: SecretStore) -> None:
        reply = run("", store)
        assert "No secrets stored" in reply
        assert "/secrets set NAME=value" in reply

    def test_list_shows_names_and_lengths(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        reply = run("", store)
        assert "TOKEN" in reply
        assert f"{len(SECRET)} chars" in reply

    def test_list_never_shows_values(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert SECRET not in run("", store)
        assert SECRET not in run("list", store)

    def test_explicit_list_verb(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert "TOKEN" in run("list", store)

    def test_list_sorted(self, store: SecretStore) -> None:
        store.set("ZULU", "aaaaaaaaaa")
        store.set("ALPHA", "bbbbbbbbbb")
        reply = run("", store)
        assert reply.index("ALPHA") < reply.index("ZULU")


class TestSet:
    def test_stores_value(self, store: SecretStore) -> None:
        reply = run(f"set TOKEN={SECRET}", store)
        assert "Stored TOKEN" in reply
        assert store.get("TOKEN") == SECRET

    def test_reply_omits_value(self, store: SecretStore) -> None:
        assert SECRET not in run(f"set TOKEN={SECRET}", store)

    def test_uppercases_name(self, store: SecretStore) -> None:
        reply = run(f"set token={SECRET}", store)
        assert "Stored TOKEN" in reply
        assert store.get("TOKEN") == SECRET

    def test_verb_is_case_insensitive(self, store: SecretStore) -> None:
        assert "Stored TOKEN" in run(f"SET TOKEN={SECRET}", store)

    def test_value_case_preserved(self, store: SecretStore) -> None:
        """The router must not fold argument case: values are case-significant."""
        store_value = "MixedCase-VALUE-123"
        run(f"set TOKEN={store_value}", store)
        assert store.get("TOKEN") == store_value

    def test_space_separated_form(self, store: SecretStore) -> None:
        run(f"set TOKEN {SECRET}", store)
        assert store.get("TOKEN") == SECRET

    def test_value_with_equals_signs(self, store: SecretStore) -> None:
        run("set TOKEN=a=b=c", store)
        assert store.get("TOKEN") == "a=b=c"

    def test_value_with_spaces(self, store: SecretStore) -> None:
        run("set TOKEN=has spaces in it", store)
        assert store.get("TOKEN") == "has spaces in it"

    def test_overwrite(self, store: SecretStore) -> None:
        run("set TOKEN=first-value", store)
        run("set TOKEN=second-value", store)
        assert store.get("TOKEN") == "second-value"

    def test_missing_value_shows_usage(self, store: SecretStore) -> None:
        assert "Usage" in run("set TOKEN", store)

    def test_missing_args_shows_usage(self, store: SecretStore) -> None:
        assert "Usage" in run("set", store)

    def test_invalid_name_rejected_without_echoing_value(self, store: SecretStore) -> None:
        reply = run(f"set BAD-NAME={SECRET}", store)
        assert SECRET not in reply
        assert "underscore" in reply
        assert store.load() == {}

    def test_reserved_name_rejected(self, store: SecretStore) -> None:
        reply = run("set PATH=/evil/bin", store)
        assert "reserved" in reply
        assert store.load() == {}

    def test_short_value_accepted(self, store: SecretStore) -> None:
        assert "Stored TOKEN" in run("set TOKEN=1111", store)
        assert store.get("TOKEN") == "1111"

    def test_advises_deleting_the_message(self, store: SecretStore) -> None:
        assert "Delete the message" in run(f"set TOKEN={SECRET}", store)


class TestDelete:
    def test_removes_secret(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert "Removed TOKEN" in run("del TOKEN", store)
        assert store.get("TOKEN") is None

    def test_lowercase_name_accepted(self, store: SecretStore) -> None:
        """Typing caps on mobile is awkward; normalization handles it."""
        store.set("TOKEN", SECRET)
        assert "Removed TOKEN" in run("del token", store)
        assert store.load() == {}

    @pytest.mark.parametrize("verb", ["del", "delete", "rm", "remove", "unset"])
    def test_delete_aliases(self, store: SecretStore, verb: str) -> None:
        store.set("TOKEN", SECRET)
        assert "Removed TOKEN" in run(f"{verb} TOKEN", store)

    def test_absent_reports_clearly(self, store: SecretStore) -> None:
        assert "No secret named ABSENT" in run("del ABSENT", store)

    def test_missing_name_shows_usage(self, store: SecretStore) -> None:
        assert "Usage" in run("del", store)

    def test_invalid_name_rejected(self, store: SecretStore) -> None:
        assert "underscore" in run("del BAD-NAME", store)


class TestGetIsRefused:
    def test_get_never_returns_a_value(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        reply = run("get TOKEN", store)
        assert SECRET not in reply
        assert "never shown" in reply


class TestUnknownVerb:
    def test_unknown_verb_shows_usage(self, store: SecretStore) -> None:
        assert "Usage" in run("frobnicate TOKEN", store)

    def test_unknown_verb_does_not_echo_input(self, store: SecretStore) -> None:
        """A mistyped verb still carries the value, so the reply must not quote it."""
        reply = run(f"sett TOKEN={SECRET}", store)
        assert SECRET not in reply

    def test_help_verb(self, store: SecretStore) -> None:
        assert "/secrets set NAME=value" in run("help", store)
