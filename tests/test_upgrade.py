"""Upgrading the files is half the job; restarting what runs them is the other.

A long-lived gateway holds the interpreter and modules it started with, so an
upgrade that only touches disk leaves the old code serving traffic — and
reporting healthy while it does. That was observed on a real host: a service ran
0.8.x for eighteen minutes after 0.9.0 was installed. These tests pin the
detection that picks the right command, and the restart that makes the upgrade
take effect.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from atom.upgrade import (
    UpgradePlan,
    detect_install_method,
    plan_upgrade,
    restart_gateway_service,
    run_upgrade,
)


class FakeRunner:
    """Records commands and replays canned results in order."""

    def __init__(self, results: list[tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._results = list(results or [])

    def __call__(self, command: list[str], **_: Any) -> Any:
        self.calls.append(list(command))
        code, out = self._results.pop(0) if self._results else (0, "")
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")


class TestDetectInstallMethod:
    """Path shape, not a package-manager interrogation.

    uv and pipx are both routinely absent from PATH in the environment they
    installed, so asking each one "is atom yours?" is slower and no more certain
    than looking at where the interpreter sits.
    """

    def test_uv_tool_needs_both_segments(self, tmp_path: Path) -> None:
        root = tmp_path / ".local/share/uv/tools/atom"
        exe = root / "bin/python"
        exe.parent.mkdir(parents=True)
        assert detect_install_method(executable=exe, prefix=root) == "uv-tool"

    def test_uv_created_plain_venv_is_not_a_tool(self, tmp_path: Path) -> None:
        """`uv venv` makes an ordinary venv; upgrading it as a tool would fail."""
        root = tmp_path / "project/.venv"
        exe = root / "bin/python"
        exe.parent.mkdir(parents=True)
        (root / "pyvenv.cfg").write_text("home = /usr\n")
        assert detect_install_method(executable=exe, prefix=root) == "venv"

    def test_pipx(self, tmp_path: Path) -> None:
        root = tmp_path / ".local/pipx/venvs/atom"
        exe = root / "bin/python"
        exe.parent.mkdir(parents=True)
        assert detect_install_method(executable=exe, prefix=root) == "pipx"

    def test_unknown_when_nothing_matches(self, tmp_path: Path) -> None:
        root = tmp_path / "opt/somewhere"
        exe = root / "bin/python"
        exe.parent.mkdir(parents=True)
        assert detect_install_method(executable=exe, prefix=root) == "unknown"

    def test_source_checkout_is_editable(self) -> None:
        """The real process: this suite runs from a checkout, so the package
        directory sits outside sys.prefix."""
        assert detect_install_method() == "editable"


class TestPlanUpgrade:
    def test_uv_tool_upgrades_by_name(self) -> None:
        plan = plan_upgrade(method="uv-tool", include_channels=False)
        assert plan.command == ("uv", "tool", "upgrade", "atom")

    def test_pinned_ref_becomes_an_install(self) -> None:
        """`uv tool upgrade` takes no source, so a tag has to go through install."""
        plan = plan_upgrade(method="uv-tool", ref="v0.9.0", include_channels=False)
        assert plan.command is not None
        assert plan.command[:4] == ("uv", "tool", "install", "--force")
        assert plan.command[4].endswith("@v0.9.0")

    def test_channel_requirements_force_the_install_form(self, monkeypatch) -> None:
        """`uv tool upgrade` rejects --with outright, so extras must go through
        `install --force`, which accepts both a source and --with.

        Found live: the upgrade form failed with "unexpected argument '--with'
        found" against uv 0.12.5.
        """
        monkeypatch.setattr(
            "atom.upgrade.channel_requirements",
            lambda: ("python-telegram-bot>=22.6,<23.0", "socksio>=1.0.0,<2.0.0"),
        )
        plan = plan_upgrade(method="uv-tool", include_channels=True)
        assert plan.command is not None
        assert plan.command[:4] == ("uv", "tool", "install", "--force")
        assert "upgrade" not in plan.command
        assert plan.command.count("--with") == 2
        assert "python-telegram-bot>=22.6,<23.0" in plan.command

    def test_plain_upgrade_form_when_there_are_no_extras(self, monkeypatch) -> None:
        """Without extras the cheaper `upgrade` form is correct and is used."""
        monkeypatch.setattr("atom.upgrade.channel_requirements", lambda: ())
        plan = plan_upgrade(method="uv-tool", include_channels=True)
        assert plan.command == ("uv", "tool", "upgrade", "atom")

    def test_channels_can_be_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "atom.upgrade.channel_requirements", lambda: ("python-telegram-bot",),
        )
        plan = plan_upgrade(method="uv-tool", include_channels=False)
        assert plan.command == ("uv", "tool", "upgrade", "atom")

    def test_editable_refuses_rather_than_guessing(self) -> None:
        plan = plan_upgrade(method="editable")
        assert not plan.can_upgrade
        assert plan.refusal is not None
        assert "git pull" in plan.refusal

    def test_unknown_refuses(self) -> None:
        """Guessing risks running a command that uninstalls a working install."""
        plan = plan_upgrade(method="unknown")
        assert not plan.can_upgrade
        assert plan.refusal is not None


class TestRunUpgrade:
    def _plan(self) -> UpgradePlan:
        return UpgradePlan("venv", (sys.executable, "-m", "pip", "install", "-U", "atom"))

    def test_restart_follows_a_real_change(self) -> None:
        restarts: list[bool] = []
        runner = FakeRunner([(0, ""), (0, "0.9.0")])
        result = run_upgrade(
            self._plan(),
            version_before="0.8.4",
            runner=runner,
            service_restarter=lambda: (restarts.append(True) or (True, "Restarted.")),
        )
        assert result.ok and result.changed
        assert result.version_after == "0.9.0"
        assert restarts == [True], "new code on disk is not new code running"

    def test_restart_happens_even_when_the_version_did_not_change(self) -> None:
        """"Already up to date" describes the files, not the process.

        The case that motivated this command was a host with new files and a
        service still running the old ones. Skipping the restart on "no change"
        would strand exactly that host permanently, so the restart is attempted
        either way — reproduced live before this was changed.
        """
        restarts: list[bool] = []
        runner = FakeRunner([(0, ""), (0, "0.8.4")])
        result = run_upgrade(
            self._plan(),
            version_before="0.8.4",
            runner=runner,
            service_restarter=lambda: (restarts.append(True) or (True, "Restarted.")),
        )
        assert result.ok and not result.changed
        assert restarts == [True]

    def test_version_is_read_from_a_fresh_process(self) -> None:
        """This process imported atom before the upgrade, so its __version__ is
        stale and every upgrade would look like a no-op."""
        runner = FakeRunner([(0, ""), (0, "0.9.0")])
        run_upgrade(self._plan(), version_before="0.8.4", runner=runner)
        assert any("import atom" in part for part in runner.calls[-1])

    def test_failed_command_reports_and_does_not_restart(self) -> None:
        restarts: list[bool] = []
        runner = FakeRunner([(1, "resolution impossible")])
        result = run_upgrade(
            self._plan(),
            version_before="0.8.4",
            runner=runner,
            service_restarter=lambda: (restarts.append(True) or (True, "Restarted.")),
        )
        assert not result.ok
        assert "resolution impossible" in result.message
        assert restarts == []

    def test_restart_failure_does_not_fail_the_upgrade(self) -> None:
        """The bytes are on disk either way; reporting failure would imply
        otherwise and invite a second upgrade."""
        runner = FakeRunner([(0, ""), (0, "0.9.0")])

        def boom() -> tuple[bool, str]:
            raise OSError("no bus")

        result = run_upgrade(
            self._plan(), version_before="0.8.4", runner=runner, service_restarter=boom,
        )
        assert result.ok
        assert result.service_restarted is False
        assert result.service_message is not None

    def test_dry_run_touches_nothing(self) -> None:
        runner = FakeRunner()
        result = run_upgrade(
            self._plan(), version_before="0.8.4", runner=runner, dry_run=True,
        )
        assert result.ok
        assert runner.calls == []

    def test_refusal_is_not_executed(self) -> None:
        runner = FakeRunner()
        result = run_upgrade(
            UpgradePlan("editable", None, refusal="source checkout"),
            version_before="0.8.4",
            runner=runner,
        )
        assert not result.ok
        assert runner.calls == []


class TestRestartGatewayService:
    def test_missing_service_is_not_a_failure(self, monkeypatch) -> None:
        """Plenty of installs run the gateway in a terminal; those pick up new
        code on their own next start."""
        monkeypatch.setattr(sys, "platform", "linux")
        runner = FakeRunner([(1, "inactive")])
        ok, message = restart_gateway_service(
            runner=runner, which=lambda _: "/usr/bin/systemctl",
        )
        assert ok is False
        assert "restart" in message.lower()

    def test_active_service_is_restarted(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        runner = FakeRunner([(0, "active"), (0, "")])
        ok, _ = restart_gateway_service(
            runner=runner, which=lambda _: "/usr/bin/systemctl",
        )
        assert ok is True
        assert runner.calls[-1][-2:] == ["restart", "atom-gateway.service"]

    def test_absent_systemctl_is_reported_not_raised(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        ok, message = restart_gateway_service(runner=FakeRunner(), which=lambda _: None)
        assert ok is False
        assert "systemctl" in message

    def test_root_uses_a_system_unit(self, monkeypatch) -> None:
        """Root has no login session on a headless host, so --user cannot reach
        a bus there. Same rule the service installer applies."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("os.geteuid", lambda: 0, raising=False)
        runner = FakeRunner([(0, "active"), (0, "")])
        restart_gateway_service(runner=runner, which=lambda _: "/usr/bin/systemctl")
        assert "--user" not in runner.calls[0]

    def test_non_root_uses_a_user_unit(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
        runner = FakeRunner([(0, "active"), (0, "")])
        restart_gateway_service(runner=runner, which=lambda _: "/usr/bin/systemctl")
        assert "--user" in runner.calls[0]

    def test_macos_uses_launchctl(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("os.geteuid", lambda: 501, raising=False)
        runner = FakeRunner([(0, ""), (0, "")])
        ok, _ = restart_gateway_service(
            runner=runner, which=lambda _: "/bin/launchctl",
        )
        assert ok is True
        assert runner.calls[-1][:3] == ["launchctl", "kickstart", "-k"]
