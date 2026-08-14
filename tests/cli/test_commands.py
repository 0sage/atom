import asyncio
import json
import re
import shutil
import signal
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from atom.agent.memory import MemoryStore
from atom.agent.tools.registry import ToolRegistry
from atom.agent.turn_delivery import TurnDeliveryFactory
from atom.bus.events import InboundMessage, OutboundMessage
from atom.cli import commands as cli_commands
from atom.cli import gateway_runtime as cli_gateway_runtime
from atom.cli import terminal as cli_terminal
from atom.cli.commands import app
from atom.config.schema import Config
from atom.cron.bound_runner import _bound_session_delivery_context
from atom.cron.service import CronJobSkippedError
from atom.cron.session_turns import CRON_DEFER_UNTIL_IDLE_META, CRON_TRIGGER_META
from atom.cron.types import CronJob, CronPayload
from atom.providers.factory import ProviderSnapshot, make_provider
from atom.providers.unconfigured_provider import UnconfiguredProvider
from atom.session.metadata_keys import (
    WEBUI_MESSAGE_SOURCE_METADATA_KEY,
    WEBUI_TURN_METADATA_KEY,
)

runner = CliRunner()


def _without_rendered_line_breaks(output: str) -> str:
    return "".join(output.splitlines())


def test_bound_cron_delivery_strips_legacy_turn_identity() -> None:
    """Legacy turn identity persisted in origin metadata must not be reused."""
    from atom.cron.types import CronJob, CronPayload

    job = CronJob(
        id="drink-water",
        name="drink water",
        payload=CronPayload(
            session_key="telegram:1",
            message="drink water",
            origin_channel="telegram",
            origin_chat_id="chat-1",
            origin_metadata={
                WEBUI_TURN_METADATA_KEY: "turn-that-created-the-reminder",
                WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron"},
                "workspace_scope": {"mode": "default"},
            },
        ),
    )

    channel, chat_id, metadata = _bound_session_delivery_context(job)

    assert (channel, chat_id) == ("telegram", "chat-1")
    assert metadata["workspace_scope"] == {"mode": "default"}
    assert WEBUI_TURN_METADATA_KEY not in metadata
    assert WEBUI_MESSAGE_SOURCE_METADATA_KEY not in metadata


def _fake_provider():
    """Return a minimal fake provider that satisfies AgentLoop.__init__."""
    p = MagicMock()
    p.generation.max_tokens = 4096
    return p


class _StopGatewayError(RuntimeError):
    pass


class _GatewayAgentContractStub:
    """Minimal stable AgentLoop surface required by gateway assembly tests."""

    tools = ToolRegistry()

    @staticmethod
    def mcp_runtime_status() -> dict[str, str]:
        return {}

    @staticmethod
    def pending_cron_job_ids_for_session(_session_key: str) -> set[str]:
        return set()

    @staticmethod
    def pending_local_trigger_ids_for_session(_session_key: str) -> set[str]:
        return set()

    async def submit_local_trigger_turn(
        self,
        _msg: InboundMessage,
    ) -> OutboundMessage | None:
        return None


def test_gateway_signal_handler_first_signal_stops_and_second_forces() -> None:
    class _FakeLoop:
        def __init__(self) -> None:
            self.handlers: dict[int, tuple[object, tuple[object, ...]]] = {}
            self.removed: list[int] = []

        def add_signal_handler(self, signum, callback, *args) -> None:
            self.handlers[int(signum)] = (callback, args)

        def remove_signal_handler(self, signum) -> bool:
            self.removed.append(int(signum))
            self.handlers.pop(int(signum), None)
            return True

    async def _run() -> None:
        loop = _FakeLoop()
        shutdown_event = asyncio.Event()
        never = asyncio.Event()
        task = asyncio.create_task(never.wait())
        output: list[str] = []

        restore = cli_gateway_runtime._install_gateway_shutdown_handlers(
            loop, shutdown_event, [task], output.append,
        )
        try:
            callback, args = loop.handlers[int(signal.SIGINT)]
            assert callable(callback)

            callback(*args)
            assert shutdown_event.is_set()
            assert output == ["\nShutting down... Press Ctrl+C again to force."]
            assert not task.done()

            callback(*args)
            await asyncio.sleep(0)
            assert task.cancelled()
        finally:
            restore()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert int(signal.SIGINT) in loop.removed
        assert int(signal.SIGTERM) in loop.removed

    asyncio.run(_run())


def test_interactive_tty_mode_restores_line_input(monkeypatch) -> None:
    try:
        import os
        import pty
        import termios
    except ImportError:  # pragma: no cover - platform without POSIX termios
        pytest.skip("termios unavailable")

    master_fd, slave_fd = pty.openpty()

    class _Stdin:
        def fileno(self) -> int:
            return slave_fd

    try:
        attrs = termios.tcgetattr(slave_fd)
        attrs[0] &= ~termios.ICRNL
        attrs[0] |= termios.IGNCR
        attrs[3] &= ~(termios.ISIG | termios.ICANON | termios.ECHO)
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

        monkeypatch.setattr(cli_terminal.sys, "stdin", _Stdin())
        cli_terminal._ensure_interactive_tty_mode()

        restored = termios.tcgetattr(slave_fd)
        assert restored[0] & termios.ICRNL
        assert not restored[0] & termios.IGNCR
        assert restored[3] & termios.ISIG
        assert restored[3] & termios.ICANON
        assert restored[3] & termios.ECHO
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_disabled_dream_cursor_only_advances_when_behind(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_history("first")
    store.append_history("second")

    cli_gateway_runtime._advance_dream_cursor_if_behind(store)
    assert store.get_last_dream_cursor() == 2

    store.set_last_dream_cursor(10)
    cli_gateway_runtime._advance_dream_cursor_if_behind(store)
    assert store.get_last_dream_cursor() == 10


def test_commit_dream_changes_skips_noop_run(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_soul("# Soul")
    store.write_memory("# Memory")
    store.git.init()
    store.git.auto_commit("initial")
    store.git.auto_commit = MagicMock(wraps=store.git.auto_commit)

    assert cli_gateway_runtime._commit_dream_changes(store) is None
    store.git.auto_commit.assert_not_called()


def test_commit_dream_changes_commits_real_edits(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.write_soul("# Soul")
    store.write_memory("# Memory")
    store.git.init()
    store.git.auto_commit("initial")
    store.write_memory("# Memory\n- Research notes")
    store.git.auto_commit = MagicMock(wraps=store.git.auto_commit)

    sha = cli_gateway_runtime._commit_dream_changes(store)

    assert sha is not None
    store.git.auto_commit.assert_called_once()
    message = store.git.auto_commit.call_args.args[0]
    assert message.startswith("dream: periodic memory consolidation\n\n")
    assert "Research notes" in message


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("atom.config.loader.get_config_path") as mock_cp, \
         patch("atom.config.loader.save_config") as mock_sc, \
         patch("atom.config.loader.load_config") as mock_lc, \
         patch("atom.cli.commands.get_workspace_path") as mock_ws:
        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_lc.side_effect = lambda _config_path=None: Config()

        def _save_config(config: Config, config_path: Path | None = None):
            target = config_path or config_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(config.model_dump(by_alias=True)), encoding="utf-8")

        mock_sc.side_effect = _save_config

        yield config_file, workspace_dir, mock_ws

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir, mock_ws = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "atom is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()
    expected_workspace = Config().workspace_path
    assert mock_ws.call_args.args == (expected_workspace,)


def test_onboard_recommends_gateway(mock_paths):
    """Default onboarding should recommend the gateway launcher."""
    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "✓ atom is ready. Run: atom gateway" in result.stdout


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_refresh_non_interactive(mock_paths):
    """Config exists, user specifies --refresh — should refresh non-interactively (no prompt)."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard", "--refresh"])

    assert result.exit_code == 0
    assert "Config already exists" not in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir, _ = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def test_onboard_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["onboard", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output
    assert "--wizard" in stripped_output
    assert "--refresh" in stripped_output
    assert "--dir" not in stripped_output


def test_status_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["status", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output


def test_status_uses_explicit_config_and_workspace(tmp_path: Path):
    config_path = tmp_path / "instance" / "config.json"
    config_workspace = tmp_path / "config-workspace"
    override_workspace = tmp_path / "override-workspace"
    config = Config()
    config.agents.defaults.workspace = str(config_workspace)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config.model_dump(mode="json", by_alias=True)))

    result = runner.invoke(
        app,
        ["status", "--config", str(config_path), "--workspace", str(override_workspace)],
    )

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    compact_output = stripped_output.replace("\n", "")
    assert str(config_path.resolve(strict=False)) in compact_output
    assert str(override_workspace) in compact_output
    assert str(config_workspace) not in compact_output


def test_onboard_interactive_discard_does_not_save_or_create_workspace(mock_paths, monkeypatch):
    config_file, workspace_dir, _ = mock_paths

    from atom.cli.onboard import OnboardResult

    monkeypatch.setattr(
        "atom.cli.onboard.run_onboard",
        lambda initial_config: OnboardResult(config=initial_config, should_save=False),
    )

    result = runner.invoke(app, ["onboard", "--wizard"])

    assert result.exit_code == 0
    assert "No changes were saved" in result.stdout
    assert not config_file.exists()
    assert not workspace_dir.exists()


def test_onboard_uses_explicit_config_and_workspace_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "instance" / "config.json"
    workspace_path = tmp_path / "workspace"

    monkeypatch.setattr("atom.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--config", str(config_path), "--workspace", str(workspace_path)],
    )

    assert result.exit_code == 0
    saved = Config.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    assert saved.workspace_path == workspace_path
    assert (workspace_path / "AGENTS.md").exists()
    stripped_output = _strip_ansi(result.stdout)
    compact_output = stripped_output.replace("\n", "")
    resolved_config = str(config_path.resolve())
    assert resolved_config in compact_output
    assert f'atom gateway -c "{resolved_config}"' in result.stdout


def test_onboard_wizard_preserves_explicit_config_in_next_steps(tmp_path, monkeypatch):
    config_path = tmp_path / "instance" / "config.json"
    workspace_path = tmp_path / "workspace"

    from atom.cli.onboard import OnboardResult

    monkeypatch.setattr(
        "atom.cli.onboard.run_onboard",
        lambda initial_config: OnboardResult(config=initial_config, should_save=True),
    )
    monkeypatch.setattr("atom.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--wizard", "--config", str(config_path), "--workspace", str(workspace_path)],
    )

    assert result.exit_code == 0
    resolved_config = str(config_path.resolve())
    assert f'atom gateway -c "{resolved_config}"' in result.stdout


def test_plugins_list_uses_explicit_config(monkeypatch, tmp_path: Path):
    from atom.channels.plugin import ChannelPlugin

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"channels": {"example": {"enabled": True}}}),
        encoding="utf-8",
    )
    plugin = ChannelPlugin(
        name="example",
        display_name="Example",
        runtime="example.runtime:ExampleChannel",
    )
    monkeypatch.setattr(
        "atom.channels.registry.discover_plugins",
        lambda enabled_names=None: (
            {"example": plugin}
            if enabled_names is None or "example" in enabled_names
            else {}
        ),
    )
    monkeypatch.setattr(
        "atom.optional_features.optional_dependency_groups",
        lambda: {},
    )

    result = runner.invoke(app, ["plugins", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "example" in stripped_output
    assert "yes" in stripped_output


def test_openai_compat_provider_passes_model_through():
    from atom.providers.openai_compat_provider import OpenAICompatProvider

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(default_model="custom/my-model")

    assert provider.get_default_model() == "custom/my-model"


def test_provider_proxy_rejects_unsupported_backend():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "anthropic",
                    "model": "anthropic/claude-opus-4-5",
                }
            },
            "providers": {
                "anthropic": {
                    "apiKey": "sk-test",
                    "proxy": "http://127.0.0.1:23458",
                }
            },
        }
    )

    with pytest.raises(ValueError, match=r"providers\.anthropic\.proxy"):
        make_provider(config)


def test_make_provider_passes_extra_headers_to_custom_provider():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "custom", "model": "gpt-4o-mini"}},
            "providers": {
                "custom": {
                    "apiKey": "test-key",
                    "apiBase": "https://example.com/v1",
                    "extraHeaders": {
                        "APP-Code": "demo-app",
                        "x-session-affinity": "sticky-session",
                    },
                }
            }
        }
    )

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_async_openai:
        provider = make_provider(config)
        asyncio.run(provider._ensure_client())

    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://example.com/v1"
    assert kwargs["default_headers"]["APP-Code"] == "demo-app"
    assert kwargs["default_headers"]["x-session-affinity"] == "sticky-session"


def test_make_provider_treats_dynamic_custom_provider_as_direct():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "my-company-api", "model": "gpt-4o-mini"}},
            "providers": {
                "my-company-api": {
                    "apiBase": "https://example.com/v1",
                }
            },
        }
    )

    with patch("atom.providers.openai_compat_provider.AsyncOpenAI") as mock_async_openai:
        provider = make_provider(config)
        asyncio.run(provider._ensure_client())

    assert provider.get_default_model() == "gpt-4o-mini"
    assert provider._spec.name == "my_company_api"
    assert provider._spec.is_direct is True
    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["api_key"] == "no-key"
    assert kwargs["base_url"] == "https://example.com/v1"


def test_make_provider_strips_dynamic_custom_route_prefix_from_request_model():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "my-company-api/gpt-4o-mini"}},
            "providers": {
                "my-company-api": {
                    "apiBase": "https://example.com/v1",
                }
            },
        }
    )

    provider = make_provider(config)

    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=None,
        max_tokens=16,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )
    body = provider._build_responses_body(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=None,
        max_tokens=16,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert config.get_provider_name() == "my-company-api"
    assert kwargs["model"] == "gpt-4o-mini"
    assert body["model"] == "gpt-4o-mini"


def test_make_provider_preserves_namespaced_model_for_forced_dynamic_provider():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "my-company-api",
                    "model": "openai/gpt-4o-mini",
                }
            },
            "providers": {
                "my-company-api": {
                    "apiBase": "https://example.com/v1",
                }
            },
        }
    )

    provider = make_provider(config)
    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=None,
        max_tokens=16,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["model"] == "openai/gpt-4o-mini"


def test_make_provider_strips_dynamic_custom_route_prefix_once():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "auto",
                    "model": "my-company-api/openai/gpt-4o-mini",
                }
            },
            "providers": {
                "my-company-api": {
                    "apiBase": "https://example.com/v1",
                }
            },
        }
    )

    provider = make_provider(config)
    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model=None,
        max_tokens=16,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["model"] == "openai/gpt-4o-mini"


def test_make_provider_rejects_dynamic_custom_provider_without_api_base():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "my-company-api", "model": "gpt-4o-mini"}},
            "providers": {
                "my-company-api": {
                    "apiKey": "sk-test",
                }
            },
        }
    )

    with pytest.raises(ValueError, match="Provider 'my-company-api' requires api_base"):
        make_provider(config)


def test_make_provider_rejects_auto_dynamic_custom_prefix_without_api_base():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "companyProxy/gpt-4o"}},
            "providers": {
                "otherProxy": {
                    "apiBase": "https://other.example.test/v1",
                },
                "companyProxy": {
                    "apiKey": "sk-company",
                },
            },
        }
    )

    with pytest.raises(ValueError, match="Provider 'companyProxy' requires api_base"):
        make_provider(config)


@pytest.fixture
def mock_agent_runtime(tmp_path):
    """Mock agent command dependencies for focused CLI tests."""
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "default-workspace")

    with patch("atom.config.loader.load_config", return_value=config) as mock_load_config, \
         patch("atom.config.loader.resolve_config_env_vars", side_effect=lambda c: c), \
         patch("atom.cli.agent.sync_workspace_templates") as mock_sync_templates, \
         patch("atom.providers.factory.make_provider", return_value=_fake_provider()), \
         patch("atom.cli.terminal._print_agent_response") as mock_print_response, \
         patch("atom.bus.queue.MessageBus"), \
         patch("atom.cron.service.CronService"), \
         patch("atom.cli.agent.AgentLoop.from_config") as mock_from_config:
        agent_loop = MagicMock()
        agent_loop.channels_config = None
        agent_loop.process_direct = AsyncMock(
            return_value=OutboundMessage(channel="cli", chat_id="direct", content="mock-response"),
        )
        agent_loop.aclose = AsyncMock(return_value=None)
        mock_from_config.return_value = agent_loop

        yield {
            "config": config,
            "load_config": mock_load_config,
            "sync_templates": mock_sync_templates,
            "from_config": mock_from_config,
            "agent_loop": agent_loop,
            "print_response": mock_print_response,
        }


def test_agent_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output


def test_agent_uses_default_config_when_no_workspace_or_config_flags(mock_agent_runtime):
    result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (None,)
    assert mock_agent_runtime["sync_templates"].call_args.args == (
        mock_agent_runtime["config"].workspace_path,
    )
    passed_config = mock_agent_runtime["from_config"].call_args.args[0]
    assert passed_config.workspace_path == mock_agent_runtime["config"].workspace_path
    mock_agent_runtime["agent_loop"].process_direct.assert_awaited_once()
    mock_agent_runtime["print_response"].assert_called_once_with(
        "mock-response", render_markdown=True, metadata={},
    )


def test_agent_uses_explicit_config_path(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)


def test_agent_config_sets_active_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    seen: dict[str, Path] = {}

    monkeypatch.setattr(
        "atom.config.loader.set_config_path",
        lambda path: seen.__setitem__("config_path", path),
    )
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.agent.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: _fake_provider())
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("atom.cron.service.CronService", lambda _store: object())

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("atom.cli.agent.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.cli.terminal._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["config_path"] == config_file.resolve()


def test_agent_uses_workspace_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "agent-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr("atom.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.agent.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: _fake_provider())
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: object())

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("atom.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("atom.cli.agent.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.cli.terminal._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["cron_store"] == config.workspace_path / "cron" / "jobs.json"


def test_agent_workspace_override_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    override = tmp_path / "override-workspace"
    config = Config()
    seen: dict[str, Path] = {}

    monkeypatch.setattr("atom.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.agent.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: _fake_provider())
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("atom.config.paths.get_cron_dir", lambda: legacy_dir)

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("atom.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("atom.cli.agent.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.cli.terminal._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "-c", str(config_file), "-w", str(override)],
    )

    assert result.exit_code == 0
    assert seen["cron_store"] == override / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (override / "cron" / "jobs.json").exists()


def test_agent_custom_config_workspace_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    custom_workspace = tmp_path / "custom-workspace"
    config = Config()
    config.agents.defaults.workspace = str(custom_workspace)
    seen: dict[str, Path] = {}

    monkeypatch.setattr("atom.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.agent.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: _fake_provider())
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("atom.config.paths.get_cron_dir", lambda: legacy_dir)

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("atom.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("atom.cli.agent.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr(
        "atom.cli.terminal._print_agent_response", lambda *_args, **_kwargs: None
    )

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["cron_store"] == custom_workspace / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (custom_workspace / "cron" / "jobs.json").exists()


def test_agent_overrides_workspace_path(mock_agent_runtime):
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(app, ["agent", "-m", "hello", "-w", str(workspace_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    passed_config = mock_agent_runtime["from_config"].call_args.args[0]
    assert passed_config.workspace_path == workspace_path


def test_agent_workspace_override_wins_over_config_workspace(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "-c", str(config_path), "-w", str(workspace_path)],
    )

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    passed_config = mock_agent_runtime["from_config"].call_args.args[0]
    assert passed_config.workspace_path == workspace_path


def test_heartbeat_retains_recent_messages_by_default():
    config = Config()

    assert config.gateway.heartbeat.keep_recent_messages == 8


@pytest.mark.parametrize(
    "content, expected",
    [
        ("", False),
        ("# Title\n\n## Active Tasks\n", False),
        ("<!--\nmulti-line\ncomment\n-->\n", False),  # block comment, not tasks
        ("<!-- single line -->\n", False),
        ("## Active Tasks\n\n- water the plants\n", True),
        ("## Active Tasks\n\n### Garden\n\n- water the plants\n", True),
        ("## Notes\n\nsome random note\n", False),
        ("stray text before any heading\n## Active Tasks\n\n- task\n", True),
        ("stray text before any heading\n", False),
    ],
)
def test_heartbeat_has_active_tasks(content, expected):
    from atom.cli.gateway_runtime import _heartbeat_has_active_tasks

    assert _heartbeat_has_active_tasks(content) is expected


def test_heartbeat_skips_bundled_template():
    from atom.cli.gateway_runtime import _heartbeat_has_active_tasks
    from atom.utils.helpers import load_bundled_template

    assert _heartbeat_has_active_tasks(load_bundled_template("HEARTBEAT.md")) is False


def test_heartbeat_target_uses_last_channel_for_unified_session():
    from atom.cli.gateway_runtime import _pick_heartbeat_target_from_sessions
    from atom.session.keys import LAST_CHANNEL_METADATA_KEY, UNIFIED_SESSION_KEY

    target = _pick_heartbeat_target_from_sessions(
        enabled_channels=["telegram", "discord"],
        sessions=[{"key": UNIFIED_SESSION_KEY}],
        unified_session_metadata={LAST_CHANNEL_METADATA_KEY: "discord:chat-42"},
    )

    assert target == ("discord", "chat-42")


@pytest.mark.parametrize(
    "metadata",
    [
        {"last_channel": "telegram:chat-42"},
        {"last_channel": "cli:direct"},
        {"last_channel": "invalid"},
    ],
)
def test_heartbeat_target_rejects_unroutable_unified_metadata(metadata):
    from atom.cli.gateway_runtime import _pick_heartbeat_target_from_sessions
    from atom.session.keys import UNIFIED_SESSION_KEY

    target = _pick_heartbeat_target_from_sessions(
        enabled_channels=["discord"],
        sessions=[{"key": UNIFIED_SESSION_KEY}],
        unified_session_metadata=metadata,
    )

    assert target == ("cli", "direct")


def _write_instance_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")
    return config_file


def _stop_gateway_provider(_config) -> object:
    raise _StopGatewayError("stop")


def _test_provider_snapshot(provider: object, config: Config) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=provider,
        model=config.agents.defaults.model,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        signature=("test",),
    )


def _patch_gateway_ports_free(monkeypatch) -> None:
    monkeypatch.setattr(
        "atom.cli.gateway_runtime._tcp_endpoint_reachable",
        lambda *_a, **_kw: False,
    )


def _patch_cli_command_runtime(
    monkeypatch,
    config: Config,
    *,
    set_config_path=None,
    sync_templates=None,
    make_provider=None,
    message_bus=None,
    session_manager=None,
    cron_service=None,
    get_cron_dir=None,
) -> None:
    provider_factory = make_provider or (lambda _config: _fake_provider())

    monkeypatch.setattr(
        "atom.config.loader.set_config_path",
        set_config_path or (lambda _path: None),
    )
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.config.loader.resolve_config_env_vars", lambda c: c)
    monkeypatch.setattr(
        "atom.cli.commands.sync_workspace_templates",
        sync_templates or (lambda _path: None),
    )
    monkeypatch.setattr(
        "atom.cli.gateway_runtime.sync_workspace_templates",
        sync_templates or (lambda _path: None),
    )
    monkeypatch.setattr(
        "atom.providers.factory.make_provider",
        provider_factory,
    )
    monkeypatch.setattr(
        "atom.providers.factory.build_provider_snapshot",
        lambda _config: _test_provider_snapshot(provider_factory(_config), _config),
    )
    monkeypatch.setattr(
        "atom.providers.factory.load_provider_snapshot",
        lambda _config_path=None: _test_provider_snapshot(provider_factory(config), config),
    )
    monkeypatch.setattr(
        "atom.cli.runtime_config._provider_setup_error",
        lambda _config: None,
    )
    _patch_gateway_ports_free(monkeypatch)

    if message_bus is not None:
        monkeypatch.setattr("atom.bus.queue.MessageBus", message_bus)
    if session_manager is not None:
        monkeypatch.setattr("atom.session.manager.SessionManager", session_manager)
    if cron_service is not None:
        monkeypatch.setattr("atom.cron.service.CronService", cron_service)
    if get_cron_dir is not None:
        monkeypatch.setattr("atom.config.paths.get_cron_dir", get_cron_dir)


def test_heartbeat_empty_response_still_retains_recent_messages(
    monkeypatch, tmp_path: Path,
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.agents.defaults.dream.enabled = True
    config.workspace_path.mkdir(parents=True)
    (config.workspace_path / "HEARTBEAT.md").write_text(
        "## Active Tasks\n\n- Check repository health\n",
        encoding="utf-8",
    )

    provider = _fake_provider()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {}

    class _FakeSession:
        def retain_recent_legal_suffix(self, limit: int) -> None:
            seen["retained_limit"] = limit

    class _FakeSessionManager:
        def __init__(self, _workspace: Path) -> None:
            self.session = _FakeSession()
            seen["heartbeat_session"] = self.session

        def get_or_create(self, key: str) -> _FakeSession:
            seen["session_key"] = key
            return self.session

        def save(self, session: _FakeSession) -> None:
            seen["saved_session"] = session

        def list_sessions(self) -> list[dict[str, str]]:
            return [{"key": "telegram:u1"}]

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job: CronJob) -> None:
            raise _StopGatewayError("stop")

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = kwargs.get("provider", object())
            self.sessions = kwargs["session_manager"]
            self.tools = {}

        async def process_direct(self, *_args, **_kwargs):
            return SimpleNamespace(content="")

        async def aclose(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeChannelManager:
        def __init__(self, *_args, **_kwargs) -> None:
            self.enabled_channels = ["telegram"]

    async def _unexpected_evaluator(*_args, **_kwargs) -> bool:
        raise AssertionError("empty heartbeat response must not be evaluated")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        make_provider=lambda _config: provider,
        message_bus=lambda: bus,
        session_manager=_FakeSessionManager,
        cron_service=_FakeCron,
    )
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr("atom.cli.gateway_runtime.evaluate_response", _unexpected_evaluator)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    cron = seen["cron"]
    response = asyncio.run(cron.on_job(CronJob(id="heartbeat", name="heartbeat")))

    assert response is None
    assert seen["session_key"] == "heartbeat"
    assert seen["retained_limit"] == config.gateway.heartbeat.keep_recent_messages
    assert seen["saved_session"] is seen["heartbeat_session"]


def _patch_serve_runtime(monkeypatch, config: Config, seen: dict[str, object]) -> None:
    pytest.importorskip("aiohttp")

    class _FakeApiApp:
        def __init__(self) -> None:
            self.on_startup: list[object] = []
            self.on_cleanup: list[object] = []

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(workspace=config.workspace_path, **extra)
        def __init__(self, **kwargs) -> None:
            seen["workspace"] = kwargs["workspace"]

        async def aclose(self) -> None:
            return None

    def _fake_create_app(
        agent_loop,
        model_name: str,
        request_timeout: float,
        api_key: str = "",
        prepare_agent=None,
    ):
        seen["agent_loop"] = agent_loop
        seen["model_name"] = model_name
        seen["request_timeout"] = request_timeout
        seen["api_key"] = api_key
        seen["prepare_agent"] = prepare_agent
        return _FakeApiApp()

    def _fake_run_app(api_app, host: str, port: int, print):
        seen["api_app"] = api_app
        seen["host"] = host
        seen["port"] = port

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
    )
    monkeypatch.setattr("atom.cli.commands.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.api.server.create_app", _fake_create_app)
    monkeypatch.setattr("aiohttp.web.run_app", _fake_run_app)


def test_gateway_uses_workspace_from_config_by_default(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        set_config_path=lambda path: seen.__setitem__("config_path", path),
        sync_templates=lambda path: seen.__setitem__("workspace", path),
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["config_path"] == config_file.resolve()
    assert seen["workspace"] == Path(config.agents.defaults.workspace)


def test_gateway_workspace_option_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    override = tmp_path / "override-workspace"
    seen: dict[str, Path] = {}

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        sync_templates=lambda path: seen.__setitem__("workspace", path),
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["workspace"] == override
    assert config.workspace_path == override


def test_gateway_uses_workspace_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
        cron_service=_StopCron,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == config.workspace_path / "cron" / "jobs.json"


def test_gateway_unbound_agent_cron_is_skipped(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    provider = _fake_provider()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {}

    monkeypatch.setattr("atom.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.gateway_runtime.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: provider)
    monkeypatch.setattr("atom.cli.runtime_config._provider_setup_error", lambda _config: None)
    _patch_gateway_ports_free(monkeypatch)
    monkeypatch.setattr(
        "atom.providers.factory.build_provider_snapshot",
        lambda _config: _test_provider_snapshot(provider, _config),
    )
    monkeypatch.setattr(
        "atom.providers.factory.load_provider_snapshot",
        lambda _config_path=None: _test_provider_snapshot(provider, config),
    )
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: bus)

    class _FakeSession:
        def __init__(self) -> None:
            self.messages = []

        def add_message(self, role: str, content: str, **kwargs) -> None:
            self.messages.append({"role": role, "content": content, **kwargs})

    class _FakeSessionManager:
        def __init__(self, _workspace: Path) -> None:
            self.session = _FakeSession()
            seen["session_manager"] = self

        def get_or_create(self, key: str) -> _FakeSession:
            seen["session_key"] = key
            return self.session

        def save(self, session: _FakeSession) -> None:
            seen["saved_session"] = session

    monkeypatch.setattr("atom.session.manager.SessionManager", _FakeSessionManager)

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = kwargs.get("provider", object())
            self.tools = {}
            seen["agent"] = self

        async def process_direct(self, *_args, **_kwargs):
            raise AssertionError("unbound cron job must not use process_direct")

        async def submit_cron_turn(self, _msg: InboundMessage):
            raise AssertionError("unbound cron job must not run as a bound cron turn")

        async def aclose(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    async def _capture_evaluate_response(
        *_args,
        **_kwargs,
    ) -> bool:
        raise AssertionError("unbound cron job must not be evaluated for delivery")

    monkeypatch.setattr("atom.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _StopAfterCronSetup)
    monkeypatch.setattr(
        "atom.cli.gateway_runtime.evaluate_response",
        _capture_evaluate_response,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    cron = seen["cron"]
    assert isinstance(cron, _FakeCron)
    assert cron.on_job is not None

    runtime_provider = object()
    agent = seen["agent"]
    agent.provider = runtime_provider
    agent.model = "runtime-model"

    job = CronJob(
        id="cron-1",
        name="stretch",
        payload=CronPayload(
            message="Remind me to stretch.",
            deliver=True,
            channel="telegram",
            to="user-1",
        ),
    )

    with pytest.raises(CronJobSkippedError, match="unbound agent cron job"):
        asyncio.run(cron.on_job(job))

    bus.publish_outbound.assert_not_awaited()


def test_gateway_bound_cron_runs_as_session_turn(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    provider = _fake_provider()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {"run_records": []}

    monkeypatch.setattr("atom.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("atom.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("atom.cli.gateway_runtime.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("atom.providers.factory.make_provider", lambda _config: provider)
    monkeypatch.setattr("atom.cli.runtime_config._provider_setup_error", lambda _config: None)
    _patch_gateway_ports_free(monkeypatch)
    monkeypatch.setattr(
        "atom.providers.factory.build_provider_snapshot",
        lambda _config: _test_provider_snapshot(provider, _config),
    )
    monkeypatch.setattr(
        "atom.providers.factory.load_provider_snapshot",
        lambda _config_path=None: _test_provider_snapshot(provider, config),
    )
    monkeypatch.setattr("atom.bus.queue.MessageBus", lambda: bus)

    class _FakeSessionManager:
        def __init__(self, _workspace: Path) -> None:
            pass

    monkeypatch.setattr("atom.session.manager.SessionManager", _FakeSessionManager)

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

        def write_run_record(self, run_id: str, record: dict[str, object]) -> None:
            seen["run_records"].append((run_id, record))

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = kwargs.get("provider", object())
            self.tools = {}
            seen["agent"] = self

        async def submit_cron_turn(self, msg: InboundMessage):
            seen["cron_msg"] = msg
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Checked the repo.",
            )

        async def aclose(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    async def _unexpected_evaluator(*_args, **_kwargs) -> bool:
        raise AssertionError("bound cron must not use legacy response evaluator")

    monkeypatch.setattr("atom.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _StopAfterCronSetup)
    monkeypatch.setattr("atom.cli.gateway_runtime.evaluate_response", _unexpected_evaluator)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGatewayError)

    cron = seen["cron"]
    job = CronJob(
        id="repo-check",
        name="Repo check",
        payload=CronPayload(
            message="Check repository health.",
            session_key="telegram:chat-1",
            origin_channel="telegram",
            origin_chat_id="chat-1",
        ),
    )

    response = asyncio.run(cron.on_job(job))

    assert response == "Checked the repo."
    msg = seen["cron_msg"]
    assert isinstance(msg, InboundMessage)
    assert msg.channel == "telegram"
    assert msg.chat_id == "chat-1"
    assert msg.sender_id == "cron"
    assert msg.session_key_override == "telegram:chat-1"
    assert "Cron job: Check repository health." in msg.content
    assert WEBUI_MESSAGE_SOURCE_METADATA_KEY not in msg.metadata
    trigger = msg.metadata[CRON_TRIGGER_META]
    assert trigger["job_id"] == "repo-check"
    assert trigger["job_name"] == "Repo check"
    assert trigger["persist_content"] == (
        "Scheduled cron job triggered: Repo check\n\nCheck repository health."
    )
    assert msg.metadata[CRON_DEFER_UNTIL_IDLE_META] is True
    statuses = [record["status"] for _run_id, record in seen["run_records"]]
    assert statuses == ["queued", "ok"]
    assert seen["run_records"][0][0] == seen["run_records"][1][0]

    discord_job = CronJob(
        id="thread-check",
        name="Thread check",
        payload=CronPayload(
            message="Check the Discord thread.",
            session_key="discord:456:thread:777",
            origin_channel="discord",
            origin_chat_id="777",
            origin_metadata={
                "context_chat_id": "456",
                "parent_channel_id": "456",
                "thread_id": "777",
            },
        ),
    )

    response = asyncio.run(cron.on_job(discord_job))

    assert response == "Checked the repo."
    msg = seen["cron_msg"]
    assert isinstance(msg, InboundMessage)
    assert msg.channel == "discord"
    assert msg.chat_id == "777"
    assert msg.session_key_override == "discord:456:thread:777"
    assert msg.metadata["context_chat_id"] == "456"
    assert msg.metadata["parent_channel_id"] == "456"
    assert msg.metadata["thread_id"] == "777"

    telegram_job = CronJob(
        id="telegram-topic",
        name="Telegram topic",
        payload=CronPayload(
            message="Check the Telegram topic.",
            session_key="telegram:-100123:topic:42",
            origin_channel="telegram",
            origin_chat_id="-100123",
            origin_metadata={"message_thread_id": 42},
        ),
    )

    response = asyncio.run(cron.on_job(telegram_job))

    assert response == "Checked the repo."
    msg = seen["cron_msg"]
    assert isinstance(msg, InboundMessage)
    assert msg.channel == "telegram"
    assert msg.chat_id == "-100123"
    assert msg.session_key_override == "telegram:-100123:topic:42"
    assert msg.metadata["message_thread_id"] == 42

    feishu_job = CronJob(
        id="feishu-topic",
        name="Feishu topic",
        payload=CronPayload(
            message="Check the Feishu topic.",
            session_key="feishu:oc_abc:om_root123",
            origin_channel="feishu",
            origin_chat_id="oc_abc",
            origin_metadata={
                "chat_type": "group",
                "message_id": "om_root123",
                "thread_id": "om_root123",
            },
        ),
    )

    response = asyncio.run(cron.on_job(feishu_job))

    assert response == "Checked the repo."
    msg = seen["cron_msg"]
    assert isinstance(msg, InboundMessage)
    assert msg.channel == "feishu"
    assert msg.chat_id == "oc_abc"
    assert msg.session_key_override == "feishu:oc_abc:om_root123"
    assert msg.metadata["message_id"] == "om_root123"
    assert msg.metadata["thread_id"] == "om_root123"


@pytest.mark.parametrize("setup_error", [None, "No API key configured"])
def test_gateway_local_trigger_queue_submits_agent_turns(
    monkeypatch,
    tmp_path: Path,
    setup_error: str | None,
) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    config.agents.defaults.dream.enabled = False
    config.gateway.heartbeat.enabled = False
    bus = MagicMock()
    seen: dict[str, object] = {}

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: bus,
        session_manager=lambda _workspace: _FakeSessionManager(),
        cron_service=lambda _store_path: _FakeCronService(),
    )

    class _FakeMemory:
        def get_latest_cursor(self) -> int:
            return 0

        def get_last_dream_cursor(self) -> int:
            return 0

        def set_last_dream_cursor(self, _cursor: int) -> None:
            return None

    class _FakeContext:
        memory = _FakeMemory()

    class _FakeSessionManager:
        def flush_all(self) -> int:
            return 0

        def list_sessions(self) -> list[dict[str, object]]:
            return []

    class _FakeCronService:
        def __init__(self) -> None:
            self.on_job = None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job) -> None:
            return None

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            seen["agent_from_config_kwargs"] = extra
            return cls(**extra)

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = _fake_provider()
            self.tools = {}
            self.context = _FakeContext()
            self.sessions = kwargs["session_manager"]
            self.submit_local_trigger_turn = AsyncMock()
            self.runtime_resolver = MagicMock()
            seen["agent"] = self

        def schedule_background(self, _coro) -> None:
            return None

        async def run(self) -> None:
            self.runtime_resolver.invalidate.assert_called_once_with()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeChannelManager:
        enabled_channels: list[str] = []

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def get_channel(self, name: str) -> object | None:
            return object() if name == "websocket" else None

        async def start_all(self) -> None:
            await asyncio.Event().wait()

        async def stop_all(self) -> None:
            return None

    async def _fake_run_local_trigger_queue(**kwargs):
        seen["local_trigger_queue_kwargs"] = kwargs
        raise _StopGatewayError("stop")

    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr(
        "atom.triggers.local_runner.run_local_trigger_queue",
        _fake_run_local_trigger_queue,
    )

    cli_commands._run_gateway(
        config,
        health_server_enabled=False,
        unconfigured_provider_error=setup_error,
    )

    agent = seen["agent"]
    agent_kwargs = seen["agent_from_config_kwargs"]
    kwargs = seen["local_trigger_queue_kwargs"]
    assert isinstance(agent_kwargs["provider"], UnconfiguredProvider) is bool(setup_error)
    refreshed_snapshot = agent_kwargs["provider_snapshot_loader"]()
    assert not isinstance(refreshed_snapshot.provider, UnconfiguredProvider)
    assert "local_trigger_store" in agent_kwargs
    assert kwargs["store"] is agent_kwargs["local_trigger_store"]
    assert "bus" not in kwargs
    assert kwargs["submit_turn"] is agent.submit_local_trigger_turn
    assert kwargs["is_channel_enabled"]("websocket") is True
    assert kwargs["is_channel_enabled"]("telegram") is False
    turn_delivery_factory = agent_kwargs["turn_delivery_factory"]
    assert isinstance(turn_delivery_factory, TurnDeliveryFactory)
    assert turn_delivery_factory.bus is bus
    assert turn_delivery_factory.route_policy is None


def test_gateway_workspace_override_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    override = tmp_path / "override-workspace"
    config = Config()
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
        cron_service=_StopCron,
        get_cron_dir=lambda: legacy_dir,
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == override / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (override / "cron" / "jobs.json").exists()


def test_gateway_custom_config_workspace_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    custom_workspace = tmp_path / "custom-workspace"
    config = Config()
    config.agents.defaults.workspace = str(custom_workspace)
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
        cron_service=_StopCron,
        get_cron_dir=lambda: legacy_dir,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == custom_workspace / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (custom_workspace / "cron" / "jobs.json").exists()


def test_migrate_cron_store_moves_legacy_file(tmp_path: Path) -> None:
    """Legacy global jobs.json is moved into the workspace on first run."""
    from atom.cli.runtime_config import _migrate_cron_store

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace_cron = config.workspace_path / "cron" / "jobs.json"

    with patch("atom.config.paths.get_cron_dir", return_value=legacy_dir):
        _migrate_cron_store(config)

    assert workspace_cron.exists()
    assert workspace_cron.read_text() == '{"jobs": []}'
    assert not legacy_file.exists()


def test_migrate_cron_store_skips_when_workspace_file_exists(tmp_path: Path) -> None:
    """Migration does not overwrite an existing workspace cron store."""
    from atom.cli.runtime_config import _migrate_cron_store

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "jobs.json").write_text('{"old": true}')

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace_cron = config.workspace_path / "cron" / "jobs.json"
    workspace_cron.parent.mkdir(parents=True)
    workspace_cron.write_text('{"new": true}')

    with patch("atom.config.paths.get_cron_dir", return_value=legacy_dir):
        _migrate_cron_store(config)

    assert workspace_cron.read_text() == '{"new": true}'


def test_gateway_uses_configured_port_when_cli_flag_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18791" in result.stdout


def test_gateway_cli_port_overrides_configured_port(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file), "--port", "18792"])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18792" in result.stdout


@pytest.mark.parametrize(
    ("host", "display_url", "warns_about_public_bind"),
    [
        ("127.0.0.1", "http://127.0.0.1:18791/health", False),
        ("0.0.0.0", "http://127.0.0.1:18791/health", True),
    ],
)
def test_gateway_health_endpoint_binds_and_serves_expected_responses(
    monkeypatch,
    tmp_path: Path,
    host: str,
    display_url: str,
    warns_about_public_bind: bool,
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.host = host
    config.gateway.port = 18791
    captured: dict[str, object] = {}

    class _FakeSessionManager:
        def flush_all(self) -> int:
            return 0

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)
        def __init__(self, **_kwargs) -> None:
            self.model = "test-model"
            self.provider = object()
            self.sessions = _FakeSessionManager()
            self.runtime_resolver = MagicMock()

        def llm_runtime(self) -> None:
            return None

        async def run(self) -> None:
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeChannelManager:
        def __init__(self, _config, _bus, **_kwargs) -> None:
            self.enabled_channels = ["telegram", "discord"]

        async def start_all(self) -> None:
            await asyncio.Event().wait()

        async def stop_all(self) -> None:
            return None

    class _FakeCronService:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job) -> None:
            return None

    class _FakeServer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def serve_forever(self) -> None:
            raise _StopGatewayError("stop")

    async def _fake_start_server(handler, host: str, port: int):
        captured["handler"] = handler
        captured["host"] = host
        captured["port"] = port
        return _FakeServer()

    class _FakeReader:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def read(self, _size: int) -> bytes:
            return self.payload

    class _FakeWriter:
        def __init__(self) -> None:
            self.output = b""
            self.closed = False

        def write(self, data: bytes) -> None:
            self.output += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
    )
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr("atom.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr("asyncio.start_server", _fake_start_server)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert result.exit_code == 0
    assert captured["host"] == host
    assert captured["port"] == 18791
    assert f"Health endpoint: {display_url}" in result.stdout
    assert ("unauthenticated health endpoint" in result.stdout) is warns_about_public_bind
    assert ("may be reachable from other devices" in result.stdout) is warns_about_public_bind
    assert ("listening on 0.0.0.0" in result.stdout) is warns_about_public_bind

    health_handler = captured["handler"]
    assert callable(health_handler)

    def _call_handler(path: str) -> tuple[str, _FakeWriter]:
        request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        writer = _FakeWriter()
        asyncio.run(health_handler(_FakeReader(request), writer))
        return writer.output.decode(), writer

    root_response, root_writer = _call_handler("/")
    assert root_writer.closed is True
    assert "HTTP/1.0 404 Not Found" in root_response
    assert "Connection: close" in root_response
    assert root_response.endswith("\r\n\r\nNot Found")

    health_response, health_writer = _call_handler("/health")
    assert health_writer.closed is True
    assert "HTTP/1.0 200 OK" in health_response
    health_body = json.loads(health_response.split("\r\n\r\n", 1)[1])
    assert health_body == {"status": "ok"}

    missing_response, missing_writer = _call_handler("/missing")
    assert missing_writer.closed is True
    assert "HTTP/1.0 404 Not Found" in missing_response
    assert missing_response.endswith("\r\n\r\nNot Found")

    if host == "127.0.0.1":
        async def _exercise_connection_limit() -> None:
            release = asyncio.Event()
            all_started = asyncio.Event()
            started = 0

            class _BlockingReader:
                async def read(self, _size: int) -> bytes:
                    nonlocal started
                    started += 1
                    if started == cli_gateway_runtime._GATEWAY_HEALTH_MAX_CONNECTIONS:
                        all_started.set()
                    await release.wait()
                    return b"GET /health HTTP/1.1\r\n\r\n"

            active_writers = [
                _FakeWriter()
                for _ in range(cli_gateway_runtime._GATEWAY_HEALTH_MAX_CONNECTIONS)
            ]
            active_tasks = [
                asyncio.create_task(health_handler(_BlockingReader(), writer))
                for writer in active_writers
            ]
            await asyncio.wait_for(all_started.wait(), timeout=1)

            overflow_writer = _FakeWriter()
            await health_handler(
                _FakeReader(b"GET /health HTTP/1.1\r\n\r\n"),
                overflow_writer,
            )
            assert overflow_writer.closed is True
            assert overflow_writer.output == b""

            release.set()
            await asyncio.gather(*active_tasks)

        asyncio.run(_exercise_connection_limit())

        class _NeverRespondingReader:
            async def read(self, _size: int) -> bytes:
                await asyncio.Event().wait()

        monkeypatch.setattr(
            cli_gateway_runtime,
            "_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS",
            0.01,
        )
        timed_out_writer = _FakeWriter()
        asyncio.run(health_handler(_NeverRespondingReader(), timed_out_writer))
        assert timed_out_writer.closed is True
        assert timed_out_writer.output == b""


def test_gateway_agent_task_owns_initial_mcp_provider_close(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791
    seen: dict[str, object] = {}

    class _FakeSessionManager:
        def flush_all(self) -> int:
            return 0

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, **_kwargs) -> None:
            self.model = "test-model"
            self.provider = object()
            self.sessions = _FakeSessionManager()
            self.runtime_resolver = MagicMock()

        def llm_runtime(self) -> None:
            return None

        async def run(self) -> None:
            seen["agent_task"] = asyncio.current_task()
            try:
                await asyncio.Event().wait()
            finally:
                seen["agent_task_cleaned_up"] = True

        async def aclose(self) -> None:
            seen["agent_closed"] = True

        def stop(self) -> None:
            seen["agent_stopped"] = True

    class _FakeMCPProvider:
        def __init__(self) -> None:
            self.connect_task: asyncio.Task | None = None
            self.close_tasks: list[asyncio.Task | None] = []

        @classmethod
        def from_config(cls, _config, _registry):
            provider = cls()
            seen["mcp_provider"] = provider
            return provider

        async def connect(self) -> None:
            self.connect_task = asyncio.current_task()

        async def aclose(self) -> None:
            self.close_tasks.append(asyncio.current_task())

        def runtime_status(self) -> dict[str, str]:
            return {}

        async def reload(self) -> dict[str, object]:
            return {"ok": True}

    class _FakeChannelManager:
        def __init__(self, _config, _bus, **_kwargs) -> None:
            self.enabled_channels = ["telegram"]

        async def start_all(self) -> None:
            await asyncio.Event().wait()

        async def stop_all(self) -> None:
            seen["channels_stopped"] = True

    class _FakeCronService:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            seen["cron_stopped"] = True

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job) -> None:
            return None

    class _FakeServer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def serve_forever(self) -> None:
            raise _StopGatewayError("stop")

    async def _fake_start_server(_handler, _host: str, _port: int):
        return _FakeServer()

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
    )
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.cli.gateway_runtime.MCPProvider", _FakeMCPProvider)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr("atom.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr("asyncio.start_server", _fake_start_server)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert result.exit_code == 0
    assert seen["agent_stopped"] is True
    assert seen["agent_closed"] is True
    assert seen["agent_task_cleaned_up"] is True
    assert seen["channels_stopped"] is True
    assert seen["cron_stopped"] is True
    mcp_provider = seen["mcp_provider"]
    assert isinstance(mcp_provider, _FakeMCPProvider)
    assert mcp_provider.connect_task is seen["agent_task"]
    assert mcp_provider.close_tasks[0] is mcp_provider.connect_task
    assert len(mcp_provider.close_tasks) == 2


def test_gateway_shutdown_event_exits_forever_runtime_tasks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791
    seen: dict[str, object] = {}
    shutdown_order: list[str] = []

    class _FakeSessionManager:
        def flush_all(self) -> int:
            return 0

    class _FakeAgentLoop(_GatewayAgentContractStub):
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, **_kwargs) -> None:
            self.model = "test-model"
            self.provider = object()
            self.sessions = _FakeSessionManager()
            self.runtime_resolver = MagicMock()

        def llm_runtime(self) -> None:
            return None

        async def run(self) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                seen["agent_task_cleaned_up"] = True

        async def aclose(self) -> None:
            seen["agent_closed"] = True

        def stop(self) -> None:
            seen["agent_stopped"] = True

    class _FakeChannelManager:
        def __init__(self, _config, _bus, **_kwargs) -> None:
            self.enabled_channels = ["websocket"]

        async def start_all(self) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                seen["channel_task_cleaned_up"] = True
                shutdown_order.append("channel_task_cleaned_up")

        async def stop_all(self) -> None:
            seen["channels_stopped"] = True
            shutdown_order.append("channels_stopped")

    class _FakeCronService:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            seen["cron_stopped"] = True

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job) -> None:
            return None

    class _FakeServer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def serve_forever(self) -> None:
            await asyncio.Event().wait()

    async def _fake_start_server(_handler, _host: str, _port: int):
        return _FakeServer()

    def _fake_install_shutdown_handlers(_loop, event, _tasks, _print_status):
        async def _trigger_shutdown() -> None:
            await asyncio.sleep(0)
            event.set()

        asyncio.create_task(_trigger_shutdown())

        def _restore() -> None:
            seen["shutdown_handlers_restored"] = True

        return _restore

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace: object(),
    )
    monkeypatch.setattr("atom.cli.gateway_runtime.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("atom.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr("atom.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr("asyncio.start_server", _fake_start_server)
    monkeypatch.setattr(
        "atom.cli.gateway_runtime._install_gateway_shutdown_handlers",
        _fake_install_shutdown_handlers,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert result.exit_code == 0
    assert seen["agent_stopped"] is True
    assert seen["agent_closed"] is True
    assert seen["agent_task_cleaned_up"] is True
    assert seen["channel_task_cleaned_up"] is True
    assert seen["channels_stopped"] is True
    assert seen["cron_stopped"] is True
    assert seen["shutdown_handlers_restored"] is True
    # Channel cleanup must run before cancellation drains the manager task.
    # DingTalk's stream SDK can otherwise swallow cancellation and reconnect.
    assert shutdown_order == ["channels_stopped", "channel_task_cleaned_up"]


def test_serve_uses_api_config_defaults_and_workspace_override(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    config.api.host = "127.0.0.2"
    config.api.port = 18900
    config.api.timeout = 45.0
    config.api.api_key = "secret"
    override_workspace = tmp_path / "override-workspace"
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(
        app,
        ["serve", "--config", str(config_file), "--workspace", str(override_workspace)],
    )

    assert result.exit_code == 0
    assert seen["workspace"] == override_workspace
    assert seen["host"] == "127.0.0.2"
    assert seen["port"] == 18900
    assert seen["request_timeout"] == 45.0
    assert seen["api_key"] == "secret"


def test_trigger_cli_queues_message_in_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from atom.triggers.local_store import LocalTriggerStore

    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    _patch_cli_command_runtime(monkeypatch, config)

    store = LocalTriggerStore(config.workspace_path)
    trigger = store.create(
        name="Review hook",
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
    )

    result = runner.invoke(
        app,
        ["trigger", "--config", str(config_file), trigger.id, "Review PR #4502"],
    )

    assert result.exit_code == 0
    assert f"Queued {trigger.id}" in result.stdout
    deliveries = store.claim_deliveries()
    assert len(deliveries) == 1
    assert deliveries[0].trigger_id == trigger.id
    assert deliveries[0].content == "Review PR #4502"


def test_serve_cli_options_override_api_config(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.api.host = "127.0.0.2"
    config.api.port = 18900
    config.api.timeout = 45.0
    config.api.api_key = "secret"
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(
        app,
        [
            "serve",
            "--config",
            str(config_file),
            "--host",
            "127.0.0.1",
            "--port",
            "18901",
            "--timeout",
            "46",
        ],
    )

    assert result.exit_code == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 18901
    assert seen["request_timeout"] == 46.0
    assert seen["api_key"] == "secret"


def test_serve_allows_loopback_without_api_key(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(app, ["serve", "--config", str(config_file)])

    assert result.exit_code == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["api_key"] == ""


def test_serve_passes_configured_api_key(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.api.api_key = " secret "
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(app, ["serve", "--config", str(config_file)])

    assert result.exit_code == 0
    assert seen["api_key"] == "secret"


def test_serve_rejects_wildcard_host_without_api_key(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(app, ["serve", "--config", str(config_file), "--host", "0.0.0.0"])

    assert result.exit_code == 1
    assert "api_key is not set" in result.stdout
    assert "workspace" not in seen
    assert "api_app" not in seen


def test_serve_rejects_specific_network_interface_without_api_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(
        app,
        ["serve", "--config", str(config_file), "--host", "192.168.1.10"],
    )

    assert result.exit_code == 1
    assert "api_key" in result.stdout
    assert "prevent unauthenticated access" in result.stdout
    assert "api_app" not in seen


def test_channels_login_requires_channel_name() -> None:
    result = runner.invoke(app, ["channels", "login"])

    assert result.exit_code == 2
