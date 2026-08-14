import os
import plistlib
import subprocess

import pytest

from atom.gateway import GatewayStartOptions
from atom.gateway.service import GatewayServiceInstaller, GatewayServiceOptions


def _tool_present(_tool: str) -> str:
    """Pretend the service manager is installed.

    The installer now refuses before writing anything when its manager is
    absent, so tests that simulate a host must say the tool is there. Without
    this, the systemd cases fail on macOS and the launchd cases fail on Linux.
    """
    return f"/usr/bin/{_tool}"


def _expected_launchd_domain() -> str:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return "gui/current"
    return f"gui/{getuid()}"


def test_systemd_install_dry_run_renders_user_unit(tmp_path):
    installer = GatewayServiceInstaller(platform_name="Linux", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(
                port=18790,
                verbose=True,
                workspace="/tmp/atom workspace",
                config_path="/tmp/atom/config.json",
            ),
            python_executable="/venv/bin/python",
        ),
        dry_run=True,
    )

    assert result.ok is True
    assert result.manager == "systemd"
    assert result.path == tmp_path / ".config/systemd/user/atom-gateway.service"
    assert ("systemctl", "--user", "daemon-reload") in result.commands
    assert ("systemctl", "--user", "enable", "atom-gateway.service") in result.commands
    assert ("systemctl", "--user", "restart", "atom-gateway.service") in result.commands
    assert result.content is not None
    assert 'WorkingDirectory="/tmp/atom workspace"' in result.content
    assert 'ExecStart=/venv/bin/python -m atom gateway --foreground --port 18790 --verbose' in result.content
    assert '--workspace "/tmp/atom workspace" --config /tmp/atom/config.json' in result.content


def test_systemd_install_writes_unit_and_runs_commands(tmp_path):
    commands: list[list[str]] = []
    workspace = tmp_path / "missing-workspace"
    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=lambda command, **_kwargs: commands.append(command),
        which=_tool_present,
    )

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(port=18790, workspace=str(workspace)),
            enable=False,
            start_now=True,
            python_executable="/python",
        )
    )

    assert result.ok is True
    assert result.path is not None
    assert result.path.exists()
    assert workspace.exists()
    assert commands == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "restart", "atom-gateway.service"],
    ]


def test_launchd_install_dry_run_renders_plist(tmp_path):
    installer = GatewayServiceInstaller(platform_name="Darwin", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(
                port=18791,
                workspace="/Users/test/.atom/workspace",
                config_path="/Users/test/.atom/config.json",
            ),
            python_executable="/opt/homebrew/bin/python3",
        ),
        dry_run=True,
    )

    assert result.ok is True
    assert result.manager == "launchd"
    assert result.path == tmp_path / "Library/LaunchAgents/ai.atom.gateway.plist"
    assert result.content is not None
    payload = plistlib.loads(result.content.encode("utf-8"))
    assert payload["Label"] == "ai.atom.gateway"
    assert payload["ProgramArguments"] == [
        "/opt/homebrew/bin/python3",
        "-m",
        "atom",
        "gateway",
        "--foreground",
        "--port",
        "18791",
        "--workspace",
        "/Users/test/.atom/workspace",
        "--config",
        "/Users/test/.atom/config.json",
    ]
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["RunAtLoad"] is True
    assert ("launchctl", "bootstrap", _expected_launchd_domain(), str(result.path)) in result.commands


def test_launchd_no_enable_start_still_bootstraps(tmp_path):
    installer = GatewayServiceInstaller(platform_name="Darwin", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(port=18790),
            enable=False,
            start_now=True,
        ),
        dry_run=True,
    )

    assert result.content is not None
    payload = plistlib.loads(result.content.encode("utf-8"))
    assert payload["RunAtLoad"] is False
    assert result.commands[0][:2] == ("launchctl", "bootstrap")
    assert not any(command[1] == "enable" for command in result.commands)
    assert any(command[1] == "kickstart" for command in result.commands)


def test_launchd_enable_without_start_sets_run_at_load_without_bootstrap(tmp_path):
    installer = GatewayServiceInstaller(platform_name="Darwin", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(port=18790),
            enable=True,
            start_now=False,
        ),
        dry_run=True,
    )

    assert result.content is not None
    payload = plistlib.loads(result.content.encode("utf-8"))
    assert payload["RunAtLoad"] is True
    assert not any(command[1] == "bootstrap" for command in result.commands)
    assert any(command[1] == "enable" for command in result.commands)
    assert not any(command[1] == "kickstart" for command in result.commands)


def test_launchd_no_enable_start_reinstall_boots_out_existing_label(tmp_path):
    commands: list[list[str]] = []
    installer = GatewayServiceInstaller(
        platform_name="Darwin",
        home=tmp_path,
        subprocess_run=lambda command, **_kwargs: commands.append(command),
        which=_tool_present,
    )

    result = installer.install(
        GatewayServiceOptions(
            start=GatewayStartOptions(port=18790),
            enable=False,
            start_now=True,
        )
    )

    assert result.ok is True
    assert commands[0][:2] == ["launchctl", "bootout"]
    assert commands[1][:2] == ["launchctl", "bootstrap"]


def test_launchd_dry_run_does_not_require_posix_getuid(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    installer = GatewayServiceInstaller(platform_name="Darwin", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(start=GatewayStartOptions(port=18790)),
        dry_run=True,
    )

    assert result.ok is True
    assert result.commands[0][:3] == ("launchctl", "bootstrap", "gui/current")


def test_uninstall_systemd_removes_unit_and_reloads(tmp_path):
    commands: list[list[str]] = []
    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=lambda command, **_kwargs: commands.append(command),
        which=_tool_present,
    )
    unit = tmp_path / ".config/systemd/user/atom-gateway.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n", encoding="utf-8")

    result = installer.uninstall()

    assert result.ok is True
    assert not unit.exists()
    assert commands == [
        ["systemctl", "--user", "disable", "--now", "atom-gateway.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_auto_manager_rejects_unsupported_platform(tmp_path):
    installer = GatewayServiceInstaller(platform_name="FreeBSD", home=tmp_path)

    result = installer.install(
        GatewayServiceOptions(start=GatewayStartOptions(port=18790)),
        dry_run=True,
    )

    assert result.ok is False
    assert result.message == "unsupported_service_manager:freebsd"


def _tool_absent(_tool: str) -> None:
    """Simulate a host without the service manager (Alpine has no systemctl)."""
    return None


def test_systemd_install_refuses_before_writing_when_systemctl_is_absent(tmp_path):
    """The orphan-unit bug: writing first left a unit nothing would ever start."""
    commands: list[list[str]] = []
    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=lambda command, **_kwargs: commands.append(command),
        which=_tool_absent,
    )

    result = installer.install(
        GatewayServiceOptions(start=GatewayStartOptions(port=18790))
    )

    assert result.ok is False
    assert result.message == "service_manager_unavailable:systemctl"
    assert commands == []
    assert not (tmp_path / ".config/systemd/user/atom-gateway.service").exists()


def test_launchd_install_refuses_before_writing_when_launchctl_is_absent(tmp_path):
    installer = GatewayServiceInstaller(
        platform_name="Darwin",
        home=tmp_path,
        subprocess_run=lambda *_a, **_k: None,
        which=_tool_absent,
    )

    result = installer.install(
        GatewayServiceOptions(start=GatewayStartOptions(port=18790))
    )

    assert result.ok is False
    assert result.message == "service_manager_unavailable:launchctl"
    assert not (tmp_path / "Library/LaunchAgents/ai.atom.gateway.plist").exists()


def test_systemd_uninstall_refuses_when_systemctl_is_absent(tmp_path):
    """Uninstall's daemon-reload uses check=True, so it would raise on Alpine."""
    unit = tmp_path / ".config/systemd/user/atom-gateway.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n", encoding="utf-8")
    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=lambda *_a, **_k: None,
        which=_tool_absent,
    )

    result = installer.uninstall()

    assert result.ok is False
    assert result.message == "service_manager_unavailable:systemctl"
    assert unit.exists()  # left alone rather than half-removed


def test_systemd_install_removes_new_unit_when_a_command_fails(tmp_path):
    """A failing systemctl must not leave a unit behind that was never enabled."""

    def _boom(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command)

    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=_boom,
        which=_tool_present,
    )

    with pytest.raises(subprocess.CalledProcessError):
        installer.install(GatewayServiceOptions(start=GatewayStartOptions(port=18790)))

    assert not (tmp_path / ".config/systemd/user/atom-gateway.service").exists()


def test_systemd_install_restores_previous_unit_when_a_command_fails(tmp_path):
    """A failed reinstall must not destroy the unit that was already working."""
    unit = tmp_path / ".config/systemd/user/atom-gateway.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\nDescription=the one that worked\n", encoding="utf-8")

    def _boom(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command)

    installer = GatewayServiceInstaller(
        platform_name="Linux",
        home=tmp_path,
        subprocess_run=_boom,
        which=_tool_present,
    )

    with pytest.raises(subprocess.CalledProcessError):
        installer.install(GatewayServiceOptions(start=GatewayStartOptions(port=18790)))

    assert unit.read_text(encoding="utf-8") == "[Unit]\nDescription=the one that worked\n"
