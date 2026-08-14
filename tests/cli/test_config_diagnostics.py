import json

import pytest
from typer.testing import CliRunner

from atom.cli.commands import app
from atom.gateway import GatewayRuntime, RuntimeResult

runner = CliRunner()

_ANTHROPIC_BACKEND_CASES = (
    ("anthropic", "anthropic", "claude-sonnet-4-5", "ANTHROPIC_API_KEY", "Anthropic"),
)


def _without_rendered_line_breaks(output: str) -> str:
    return "".join(output.splitlines())


def _write_ready_config(config_path, *, channels: dict | None = None) -> None:
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "claude-sonnet-4-5",
                        "provider": "anthropic",
                    }
                },
                "providers": {
                    "anthropic": {
                        "apiKey": "sk-ant-test",
                    }
                },
                "channels": channels or {},
            }
        ),
        encoding="utf-8",
    )


def test_status_reports_ready_provider_and_next_step(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_ready_config(config_path)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Agent: ✓ provider/model configuration is ready" in result.stdout
    assert "Anthropic:" in result.stdout
    assert "Model: claude-sonnet-4-5" in result.stdout
    assert 'atom agent -m "Hello!"' in result.stdout
    assert "Status does not call the model" in result.stdout


def test_status_validates_provider_without_constructing_provider(
    tmp_path,
    monkeypatch,
) -> None:
    from atom.providers.anthropic_provider import AnthropicProvider

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "claude-sonnet-4-5",
                        "provider": "anthropic",
                    }
                },
                "providers": {"anthropic": {"apiKey": "sk-ant-test"}},
            }
        ),
        encoding="utf-8",
    )

    def _unexpected_init(*_args, **_kwargs) -> None:
        pytest.fail("status must not construct a provider client")

    monkeypatch.setattr(AnthropicProvider, "__init__", _unexpected_init)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Agent: ✓ provider/model configuration is ready" in result.stdout
    assert "Status does not call the model or verify network access" in result.stdout


@pytest.mark.parametrize(
    ("provider", "provider_key", "model", "env_name", "label"),
    _ANTHROPIC_BACKEND_CASES,
)
def test_status_reports_missing_key_for_anthropic_backends(
    tmp_path,
    monkeypatch,
    provider: str,
    provider_key: str,
    model: str,
    env_name: str,
    label: str,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(env_name, raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": model, "provider": provider}},
                "providers": {provider_key: {}},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])
    output = _without_rendered_line_breaks(result.stdout)

    assert result.exit_code == 0
    assert f"Agent: ✗ No API key configured for provider '{provider}'." in output
    assert f"{label}: not set" in output
    assert "provider/model configuration is ready" not in output
    assert 'Next: atom agent -m "Hello!"' not in output
    assert "atom onboard --wizard" in output


@pytest.mark.parametrize(
    ("provider", "provider_key", "model", "env_name", "label"),
    _ANTHROPIC_BACKEND_CASES,
)
def test_status_accepts_resolved_key_for_anthropic_backends(
    tmp_path,
    monkeypatch,
    provider: str,
    provider_key: str,
    model: str,
    env_name: str,
    label: str,
) -> None:
    monkeypatch.setenv(env_name, "test-api-key")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": model, "provider": provider}},
                "providers": {provider_key: {"apiKey": f"${{{env_name}}}"}},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Agent: ✓ provider/model configuration is ready" in result.stdout
    assert f"{label}: ✓" in result.stdout
    assert 'atom agent -m "Hello!"' in result.stdout


def test_status_reports_missing_provider_with_shortest_setup_routes(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Agent: ✗" in result.stdout
    assert "No provider is configured for model" in result.stdout
    assert "atom onboard --wizard" in result.stdout
    assert "atom status --config" in result.stdout


def test_status_readiness_does_not_validate_channel_configuration(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_ready_config(
        config_path,
        channels={"websocket": {"enabled": False, "path": "missing-slash"}},
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Agent: ✓ provider/model configuration is ready" in result.stdout
    assert "channels.websocket" not in result.stdout


def test_status_reports_json_location_without_traceback(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{broken", encoding="utf-8")

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Invalid configuration" in result.stdout
    assert "JSON syntax error at line 1, column 2" in result.stdout
    assert "Traceback" not in result.stdout


def test_status_reports_field_without_exposing_secret(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    secret = "should-never-appear"
    config_path.write_text(
        json.dumps({"providers": {"groq": {"apiKey": [secret]}}}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "providers.groq.apiKey" in result.stdout
    assert secret not in result.stdout
    assert "input_value" not in result.stdout
    assert "errors.pydantic.dev" not in result.stdout


def test_status_reports_missing_env_var_at_field(tmp_path, monkeypatch) -> None:
    name = "ATOM_TEST_STATUS_MISSING"
    monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"providers": {"groq": {"apiKey": f"${{{name}}}"}}}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "providers.groq.apiKey" in result.stdout
    assert name in result.stdout
    assert "Groq: not set" in result.stdout
    assert "Groq: ✓" not in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["agent", "--message", "hello"],
    ],
)
def test_agent_entrypoints_point_invalid_config_to_status(tmp_path, args: list[str]) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{broken", encoding="utf-8")

    result = runner.invoke(app, [*args, "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Invalid configuration" in result.stdout
    assert "atom status --config" in result.stdout
    assert "Traceback" not in result.stdout


def test_agent_provider_setup_failure_points_to_shortest_routes(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"workspace": str(workspace)}}}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["agent", "--message", "hello", "--config", str(config_path)],
    )
    output = _without_rendered_line_breaks(result.stdout)

    assert result.exit_code == 1
    assert "Agent cannot start: No provider is configured for model" in output
    assert "atom onboard --wizard" in output
    assert "atom status --config" in output
    assert "Traceback" not in output
    assert not workspace.exists()


@pytest.mark.parametrize(
    "args",
    [
        ["gateway"],
        ["gateway", "--background"],
        ["gateway", "restart"],
    ],
)
def test_gateway_provider_setup_failure_points_to_shortest_routes(
    tmp_path,
    monkeypatch,
    args: list[str],
) -> None:
    config_path = tmp_path / "explicit-gateway-config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"workspace": str(workspace)}},
                "channels": {"websocket": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )

    def unexpected_managed_start(*_args, **_kwargs) -> RuntimeResult:
        pytest.fail("provider validation must fail before a managed gateway start")

    monkeypatch.setattr(GatewayRuntime, "start_background", unexpected_managed_start)
    monkeypatch.setattr(GatewayRuntime, "restart", unexpected_managed_start)

    result = runner.invoke(app, [*args, "--config", str(config_path)])
    output = _without_rendered_line_breaks(result.stdout)

    assert result.exit_code == 1
    assert "Gateway cannot start: No provider is configured for model" in output
    assert "atom onboard --wizard" in output
    assert "atom status --config" in output
    assert config_path.name in output
    assert "Traceback" not in output
    assert not workspace.exists()



def test_status_missing_file_points_to_setup_without_changing_exit_contract(tmp_path) -> None:
    config_path = tmp_path / "missing.json"

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "configuration file not found" in result.stdout
    assert "atom onboard --wizard" in result.stdout
