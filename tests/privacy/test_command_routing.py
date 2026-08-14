"""``/secrets`` must route through the priority tier.

The priority tier is the only dispatch path that runs without a session
(``loop.py:_dispatch_command_inline``), so it is the only one where neither the
command text nor the reply reaches session history. If these tests fail, secret
values are being written to disk in the session transcript.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from atom.command.builtin import register_builtin_commands
from atom.command.router import CommandContext, CommandRouter

SECRET = "ghp_s3cr3t_value_9999"


@pytest.fixture
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture
def secrets_store(tmp_path, monkeypatch):
    from atom.privacy import store as store_module

    replacement = store_module.SecretStore(path=tmp_path / "secrets.env")
    monkeypatch.setattr(store_module, "DEFAULT_SECRET_STORE", replacement)
    return replacement


def make_ctx(raw: str) -> CommandContext:
    msg = MagicMock(channel="telegram", chat_id="chat1", metadata={})
    return CommandContext(
        msg=msg, session=None, key="telegram:chat1", raw=raw, loop=MagicMock(),
    )


class TestPriorityTierRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "/secrets",
            "/secret",
            "/secrets list",
            f"/secrets set TOKEN={SECRET}",
            "/secrets del TOKEN",
            "/secret set TOKEN=x",
        ],
    )
    def test_recognized_as_priority(self, router: CommandRouter, text: str) -> None:
        assert router.is_priority(text)

    @pytest.mark.parametrize(
        "text",
        [
            "/secrets",
            "/secrets list",
            f"/secrets set TOKEN={SECRET}",
        ],
    )
    def test_excluded_from_history_writing_dispatch(
        self, router: CommandRouter, text: str,
    ) -> None:
        """is_dispatchable_command() gates the path that persists to history."""
        assert not router.is_dispatchable_command(text)

    def test_bot_suffix_stripped(self, router: CommandRouter) -> None:
        """Telegram sends /cmd@botname in groups."""
        assert router.is_priority("/secrets@atombot set TOKEN=x")

    def test_leading_whitespace_tolerated(self, router: CommandRouter) -> None:
        assert router.is_priority("  /secrets set TOKEN=x  ")

    def test_uppercase_command_name(self, router: CommandRouter) -> None:
        assert router.is_priority("/SECRETS set TOKEN=x")


class TestPriorityDispatch:
    @pytest.mark.asyncio
    async def test_list_dispatches(self, router: CommandRouter, secrets_store) -> None:
        result = await router.dispatch_priority(make_ctx("/secrets"))
        assert result is not None
        assert "No secrets stored" in result.content

    @pytest.mark.asyncio
    async def test_set_dispatches_and_stores(
        self, router: CommandRouter, secrets_store,
    ) -> None:
        result = await router.dispatch_priority(
            make_ctx(f"/secrets set TOKEN={SECRET}")
        )
        assert result is not None
        assert "Stored TOKEN" in result.content
        assert SECRET not in result.content
        assert secrets_store.get("TOKEN") == SECRET

    @pytest.mark.asyncio
    async def test_value_case_survives_dispatch(
        self, router: CommandRouter, secrets_store,
    ) -> None:
        """dispatch_priority lowercases the command name; it must not touch args."""
        value = "MixedCase-VALUE-123"
        await router.dispatch_priority(make_ctx(f"/secrets set TOKEN={value}"))
        assert secrets_store.get("TOKEN") == value

    @pytest.mark.asyncio
    async def test_singular_alias_dispatches(
        self, router: CommandRouter, secrets_store,
    ) -> None:
        result = await router.dispatch_priority(
            make_ctx(f"/secret set TOKEN={SECRET}")
        )
        assert result is not None
        assert secrets_store.get("TOKEN") == SECRET

    @pytest.mark.asyncio
    async def test_delete_dispatches(self, router: CommandRouter, secrets_store) -> None:
        secrets_store.set("TOKEN", SECRET)
        result = await router.dispatch_priority(make_ctx("/secrets del token"))
        assert result is not None
        assert "Removed TOKEN" in result.content
        assert secrets_store.get("TOKEN") is None

    @pytest.mark.asyncio
    async def test_reply_renders_as_plain_text(
        self, router: CommandRouter, secrets_store,
    ) -> None:
        """A value can contain markdown metacharacters; don't let a channel parse it."""
        result = await router.dispatch_priority(make_ctx("/secrets"))
        assert result is not None
        assert result.metadata["render_as"] == "text"

    @pytest.mark.asyncio
    async def test_reply_targets_the_originating_chat(
        self, router: CommandRouter, secrets_store,
    ) -> None:
        result = await router.dispatch_priority(make_ctx("/secrets"))
        assert result is not None
        assert result.channel == "telegram"
        assert result.chat_id == "chat1"


class TestExistingPriorityCommandsUnaffected:
    """The new tier must not shadow the exact-match priority commands."""

    def test_stop_still_priority(self, router: CommandRouter) -> None:
        assert router.is_priority("/stop")
        assert not router.is_dispatchable_command("/stop")

    def test_status_with_args_still_dispatchable(self, router: CommandRouter) -> None:
        """/status takes no args, so the arg form must still reach the rejecter."""
        assert not router.is_priority("/status now")
        assert router.is_dispatchable_command("/status now")

    def test_unrelated_prefix_not_captured(self, router: CommandRouter) -> None:
        assert not router.is_priority("/model fast")
        assert router.is_dispatchable_command("/model fast")


class TestHelpAndPalette:
    def test_appears_in_help(self) -> None:
        from atom.command.builtin import build_help_text

        assert "/secrets" in build_help_text()

    def test_appears_in_palette_with_args(self) -> None:
        from atom.command.builtin import builtin_command_palette

        entry = next(
            item for item in builtin_command_palette() if item["command"] == "/secrets"
        )
        assert entry["accepts_args"] is True

    def test_typo_suggests_the_command(self, router: CommandRouter) -> None:
        """/secrts is not a priority match, so it falls through to the rejecter."""
        assert router.is_dispatchable_command("/secrts")
