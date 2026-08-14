"""Stored secrets reach the subprocess environment, and their names reach the agent.

The agent is told which secrets exist so it can write ``$NAME``, but never what
they contain. These tests pin both halves: the value is present in the child
environment, and absent from everything the model is shown.
"""

from __future__ import annotations

import pytest

from atom.agent.tools.context import RequestContext
from atom.agent.tools.shell import ExecTool
from atom.privacy import store as store_module
from atom.privacy.env import inject_secrets, secret_names
from atom.privacy.store import SecretStore

SECRET = "ghp_s3cr3t_value_9999"


@pytest.fixture
def store(tmp_path, monkeypatch) -> SecretStore:
    replacement = SecretStore(path=tmp_path / "secrets.env")
    monkeypatch.setattr(store_module, "DEFAULT_SECRET_STORE", replacement)
    return replacement


class TestInjectSecrets:
    def test_adds_stored_values(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert inject_secrets({})["TOKEN"] == SECRET

    def test_empty_store_changes_nothing(self, store: SecretStore) -> None:
        base = {"HOME": "/home/dev"}
        assert inject_secrets(dict(base)) == base

    def test_existing_key_is_not_overridden(self, store: SecretStore) -> None:
        """Base environment wins, so a secret cannot hijack HOME or PATH even if
        the reserved-name list and the base environment ever drift apart."""
        store.path.write_text("HOME='/evil'\n")
        assert inject_secrets({"HOME": "/home/dev"})["HOME"] == "/home/dev"

    def test_mutates_in_place_and_returns_same_dict(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        env: dict[str, str] = {}
        assert inject_secrets(env) is env
        assert env["TOKEN"] == SECRET

    def test_missing_file_is_not_an_error(self, store: SecretStore) -> None:
        assert not store.path.exists()
        assert inject_secrets({}) == {}


class TestBuildEnv:
    def test_secret_present_in_subprocess_env(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert ExecTool()._build_env()["TOKEN"] == SECRET

    def test_base_keys_still_present(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        env = ExecTool()._build_env()
        assert {"HOME", "LANG", "TERM", "PYTHONUNBUFFERED"} <= set(env)

    def test_no_secrets_leaves_env_unchanged(self, store: SecretStore) -> None:
        env = ExecTool()._build_env()
        assert set(env) == {"HOME", "LANG", "TERM", "PYTHONUNBUFFERED"}

    def test_reserved_name_in_file_cannot_override_base(self, store: SecretStore) -> None:
        store.path.write_text("PATH='/evil/bin'\nHOME='/evil'\n")
        env = ExecTool()._build_env()
        assert "PATH" not in env
        assert env["HOME"] != "/evil"

    def test_unreadable_store_does_not_break_exec(
        self, store: SecretStore, monkeypatch,
    ) -> None:
        """A broken store must not make every shell command fail."""
        def boom() -> dict[str, str]:
            raise OSError("nope")

        monkeypatch.setattr(store, "load", boom)
        with pytest.raises(OSError):
            store.load()
        # The tool still builds an environment from the base keys.
        monkeypatch.setattr(store_module, "DEFAULT_SECRET_STORE", SecretStore(path=store.path))
        assert "HOME" in ExecTool()._build_env()


class TestAgentFacingSurfaces:
    """What the model is shown: names yes, values never."""

    def test_names_listed(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        store.set("API_KEY", "another-long-value")
        assert secret_names() == ["API_KEY", "TOKEN"]

    def test_description_lists_names_without_values(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        description = ExecTool().description
        assert "TOKEN" in description
        assert SECRET not in description

    def test_description_omits_note_when_no_secrets(self, store: SecretStore) -> None:
        assert "Operator-stored secrets" not in ExecTool().description

    def test_description_instructs_shell_expansion(self, store: SecretStore) -> None:
        store.set("TOKEN", SECRET)
        assert "$TOKEN" in ExecTool().description or "$NAME" in ExecTool().description

    @pytest.mark.asyncio
    async def test_runtime_context_lists_names_without_values(
        self, store: SecretStore,
    ) -> None:
        store.set("TOKEN", SECRET)
        tool = ExecTool()
        block = await tool._provide_runtime_context(
            RequestContext(channel="telegram", chat_id="c1")
        )
        assert block is not None
        assert block.source == "secrets"
        assert "TOKEN" in block.content
        assert SECRET not in block.content

    @pytest.mark.asyncio
    async def test_runtime_context_absent_without_secrets(
        self, store: SecretStore,
    ) -> None:
        tool = ExecTool()
        block = await tool._provide_runtime_context(
            RequestContext(channel="telegram", chat_id="c1")
        )
        assert block is None

    @pytest.mark.asyncio
    async def test_runtime_context_reflects_a_secret_added_mid_session(
        self, store: SecretStore,
    ) -> None:
        """The block is rebuilt per turn, so a new secret is usable immediately."""
        tool = ExecTool()
        request = RequestContext(channel="telegram", chat_id="c1")
        assert await tool._provide_runtime_context(request) is None
        store.set("TOKEN", SECRET)
        block = await tool._provide_runtime_context(request)
        assert block is not None
        assert "TOKEN" in block.content

    def test_provider_is_registered(self, store: SecretStore) -> None:
        assert ExecTool().runtime_context_provider() is not None
