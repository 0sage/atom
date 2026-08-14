"""Secret store: validation, atomic writes, and file permissions."""

from __future__ import annotations

import stat

import pytest

from atom.privacy.store import (
    MAX_NAME_LENGTH,
    SecretError,
    SecretStore,
    validate_name,
    validate_value,
)


@pytest.fixture
def store(tmp_path) -> SecretStore:
    return SecretStore(path=tmp_path / "secrets.env")


class TestValidateName:
    def test_uppercases_input(self) -> None:
        assert validate_name("token") == "TOKEN"
        assert validate_name("  my_token  ") == "MY_TOKEN"

    def test_accepts_digits_and_underscore(self) -> None:
        assert validate_name("AWS_KEY_2") == "AWS_KEY_2"
        assert validate_name("_INTERNAL") == "_INTERNAL"

    def test_rejects_empty(self) -> None:
        with pytest.raises(SecretError, match="required"):
            validate_name("   ")

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(SecretError, match="must not start with a digit"):
            validate_name("2FA_KEY")

    @pytest.mark.parametrize("name", ["MY-TOKEN", "MY TOKEN", "MY.TOKEN", "TOKEN$", "TOK;EN"])
    def test_rejects_punctuation(self, name: str) -> None:
        with pytest.raises(SecretError):
            validate_name(name)

    def test_rejects_overlong(self) -> None:
        with pytest.raises(SecretError, match="at most"):
            validate_name("A" * (MAX_NAME_LENGTH + 1))

    def test_accepts_max_length(self) -> None:
        name = "A" * MAX_NAME_LENGTH
        assert validate_name(name) == name

    @pytest.mark.parametrize("name", ["PATH", "HOME", "LD_PRELOAD", "PYTHONPATH"])
    def test_rejects_reserved(self, name: str) -> None:
        with pytest.raises(SecretError, match="reserved"):
            validate_name(name)

    def test_rejects_reserved_via_lowercase(self) -> None:
        """Normalization must happen before the reserved check, not after."""
        with pytest.raises(SecretError, match="reserved"):
            validate_name("path")


class TestValidateValue:
    def test_accepts_arbitrary_characters(self) -> None:
        value = "ghp_aB3$%^&*()'\"\\ /:@?=+"
        assert validate_value(value) == value

    def test_rejects_empty(self) -> None:
        with pytest.raises(SecretError, match="required"):
            validate_value("")

    @pytest.mark.parametrize("value", ["a\nADMIN=b", "a\rb", "a\r\nb"])
    def test_rejects_line_breaks(self, value: str) -> None:
        """A newline would write a second assignment from one command."""
        with pytest.raises(SecretError, match="line breaks"):
            validate_value(value)

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(SecretError, match="null"):
            validate_value("a\x00b")


class TestRoundTrip:
    def test_set_then_get(self, store: SecretStore) -> None:
        store.set("TOKEN", "1111")
        assert store.get("TOKEN") == "1111"

    def test_get_is_case_insensitive(self, store: SecretStore) -> None:
        store.set("token", "1111")
        assert store.get("TOKEN") == "1111"
        assert store.get("token") == "1111"

    def test_missing_returns_none(self, store: SecretStore) -> None:
        assert store.get("ABSENT") is None

    def test_load_empty_when_file_absent(self, store: SecretStore) -> None:
        assert store.load() == {}
        assert store.names() == []

    @pytest.mark.parametrize(
        "value",
        [
            "simple",
            "with spaces",
            "with'single'quotes",
            'with"double"quotes',
            "with$dollar and `backtick`",
            "with\\backslash",
            "with#hash",
            "trailing ",
            "=leading-equals",
            "a=b=c",
        ],
    )
    def test_values_survive_quoting(self, store: SecretStore, value: str) -> None:
        store.set("TOKEN", value)
        assert store.get("TOKEN") == value

    def test_names_sorted(self, store: SecretStore) -> None:
        store.set("ZULU", "1")
        store.set("ALPHA", "2")
        assert store.names() == ["ALPHA", "ZULU"]

    def test_overwrite_replaces_in_place(self, store: SecretStore) -> None:
        store.set("A", "1")
        store.set("B", "2")
        store.set("A", "3")
        assert store.load() == {"A": "3", "B": "2"}
        # The updated entry keeps its original position rather than moving down.
        lines = [ln for ln in store.path.read_text().splitlines() if ln.strip()]
        assert lines[0].startswith("A=")

    def test_overwrite_does_not_duplicate(self, store: SecretStore) -> None:
        store.set("A", "1")
        store.set("A", "2")
        assert sum(1 for ln in store.path.read_text().splitlines() if ln.startswith("A=")) == 1


class TestDelete:
    def test_delete_removes_entry(self, store: SecretStore) -> None:
        store.set("TOKEN", "1111")
        assert store.delete("TOKEN") == "TOKEN"
        assert store.get("TOKEN") is None

    def test_delete_is_case_insensitive(self, store: SecretStore) -> None:
        store.set("TOKEN", "1111")
        assert store.delete("token") == "TOKEN"
        assert store.load() == {}

    def test_delete_absent_returns_none(self, store: SecretStore) -> None:
        assert store.delete("ABSENT") is None

    def test_delete_absent_leaves_file_untouched(self, store: SecretStore) -> None:
        store.set("KEEP", "1")
        before = store.path.read_text()
        assert store.delete("ABSENT") is None
        assert store.path.read_text() == before

    def test_delete_keeps_siblings(self, store: SecretStore) -> None:
        store.set("A", "1")
        store.set("B", "2")
        store.delete("A")
        assert store.load() == {"B": "2"}

    def test_delete_rejects_invalid_name(self, store: SecretStore) -> None:
        with pytest.raises(SecretError):
            store.delete("BAD-NAME")


class TestFileFormat:
    def test_comments_and_blank_lines_survive(self, store: SecretStore) -> None:
        store.path.write_text("# my notes\n\nEXISTING='keep'\n# trailing note\n")
        store.set("ADDED", "new")
        content = store.path.read_text()
        assert "# my notes" in content
        assert "# trailing note" in content
        assert store.load() == {"EXISTING": "keep", "ADDED": "new"}

    def test_comments_survive_delete(self, store: SecretStore) -> None:
        store.path.write_text("# note\nA='1'\nB='2'\n")
        store.delete("A")
        content = store.path.read_text()
        assert "# note" in content
        assert store.load() == {"B": "2"}

    def test_reads_unquoted_hand_edit(self, store: SecretStore) -> None:
        store.path.write_text("TOKEN=plain\n")
        assert store.get("TOKEN") == "plain"

    def test_reads_double_quoted_hand_edit(self, store: SecretStore) -> None:
        store.path.write_text('TOKEN="quoted"\n')
        assert store.get("TOKEN") == "quoted"

    def test_reads_export_prefix(self, store: SecretStore) -> None:
        store.path.write_text("export TOKEN='exported'\n")
        assert store.get("TOKEN") == "exported"

    def test_ignores_lowercase_hand_edit(self, store: SecretStore) -> None:
        """A lowercase entry must never become an injected env var."""
        store.path.write_text("lower=value\nUPPER='ok'\n")
        assert store.load() == {"UPPER": "ok"}

    def test_ignores_reserved_hand_edit(self, store: SecretStore) -> None:
        """A hand-written PATH must not reach injection even though validation
        is bypassed by editing the file directly."""
        store.path.write_text("PATH='/evil/bin'\nTOKEN='ok'\n")
        assert store.load() == {"TOKEN": "ok"}

    def test_later_assignment_wins(self, store: SecretStore) -> None:
        store.path.write_text("TOKEN='first'\nTOKEN='second'\n")
        assert store.get("TOKEN") == "second"

    def test_file_ends_with_single_newline(self, store: SecretStore) -> None:
        store.set("A", "1")
        content = store.path.read_text()
        assert content.endswith("\n")
        assert not content.endswith("\n\n")

    def test_ignores_line_without_equals(self, store: SecretStore) -> None:
        store.path.write_text("GARBAGE\nTOKEN='ok'\n")
        assert store.load() == {"TOKEN": "ok"}


class TestPermissions:
    def test_new_file_is_owner_only(self, store: SecretStore) -> None:
        store.set("TOKEN", "1111")
        mode = stat.S_IMODE(store.path.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {mode:o}"

    def test_write_tightens_loose_mode(self, store: SecretStore) -> None:
        store.path.write_text("A='1'\n")
        store.path.chmod(0o644)
        store.set("B", "2")
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    def test_loose_mode_warns_but_still_loads(self, store: SecretStore, caplog) -> None:
        store.path.write_text("TOKEN='1111'\n")
        store.path.chmod(0o644)
        assert store.get("TOKEN") == "1111"

    def test_no_temp_files_left_behind(self, store: SecretStore) -> None:
        store.set("TOKEN", "1111")
        leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
        assert leftovers == []


class TestErrorMessagesNeverLeakValues:
    """Rejection messages reach the chat, so they must not quote the value."""

    def test_line_break_message_omits_value(self) -> None:
        secret = "s3cr3t-value"
        with pytest.raises(SecretError) as exc:
            validate_value(f"{secret}\nX=y")
        assert secret not in str(exc.value)

    def test_set_rejection_omits_value(self, store: SecretStore) -> None:
        secret = "s3cr3t-value"
        with pytest.raises(SecretError) as exc:
            store.set("BAD-NAME", secret)
        assert secret not in str(exc.value)

    def test_nothing_written_when_value_rejected(self, store: SecretStore) -> None:
        with pytest.raises(SecretError):
            store.set("TOKEN", "bad\nvalue")
        assert not store.path.exists()
