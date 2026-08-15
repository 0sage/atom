"""Upgrade an installed atom, then restart what is running it.

Split from the CLI command so the decisions are testable without a terminal.

The problem this solves is not "run the upgrade command" — ``uv tool upgrade``
already does that. It is that upgrading the files on disk leaves a *running*
gateway executing the old code, with nothing saying so. Observed: a service kept
serving 0.8.x for eighteen minutes after 0.9.0 was installed, reporting healthy
the whole time, because a long-lived process holds the interpreter and modules it
started with.

So an upgrade is two steps that have to happen together, and the second one is
the easy one to forget.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, cast

from loguru import logger

#: How atom got onto this machine. Each needs a different upgrade command, and
#: guessing wrong is worse than refusing: a wrong command can uninstall a working
#: install or silently do nothing.
InstallMethod = Literal["uv-tool", "pipx", "venv", "editable", "unknown"]

DEFAULT_REPO = "https://github.com/0sage/atom.git"


@dataclass(frozen=True)
class UpgradePlan:
    """What an upgrade would do, resolvable without doing any of it."""

    method: InstallMethod
    command: tuple[str, ...] | None
    #: Channel dependencies to reinstall alongside atom. ``uv tool upgrade``
    #: rebuilds the tool environment from atom's own metadata, which drops
    #: anything injected later — the gateway repairs this on next start, but
    #: passing them here means the channel never goes dark at all.
    extra_requirements: tuple[str, ...] = ()
    #: Set when the method is understood but cannot be upgraded in place.
    refusal: str | None = None

    @property
    def can_upgrade(self) -> bool:
        return self.command is not None


@dataclass
class UpgradeResult:
    ok: bool
    method: InstallMethod
    message: str
    version_before: str
    version_after: str | None = None
    commands: list[tuple[str, ...]] = field(default_factory=list)
    service_restarted: bool = False
    service_message: str | None = None

    @property
    def changed(self) -> bool:
        return (
            self.version_after is not None
            and self.version_after != self.version_before
        )


def detect_install_method(
    *,
    executable: Path | None = None,
    prefix: Path | None = None,
    package_root: Path | None = None,
) -> InstallMethod:
    """Infer how the running atom was installed, from where its files live.

    Path-shape inspection rather than asking a package manager: ``uv`` and
    ``pipx`` both may be absent from PATH in the very environment they installed,
    and shelling out to each in turn to ask "is atom yours?" is slower and no
    more certain than looking at where the interpreter actually sits.
    """
    exe = Path(executable or sys.executable).resolve()
    root = Path(prefix or sys.prefix).resolve()
    parts = {part.lower() for part in (*exe.parts, *root.parts)}

    # An editable install points site-packages back at a source tree, so the
    # package directory is not under the environment at all. Only consulted when
    # the caller did not name a package root: a synthetic prefix in a test never
    # contains the real package, which would make every case look editable.
    if package_root is None and executable is None and prefix is None:
        try:
            here = Path(__file__).resolve().parent.parent
            if not str(here).startswith(str(root)):
                return "editable"
        except OSError:  # pragma: no cover — resolve() on a broken mount
            pass
    elif package_root is not None and not str(
        Path(package_root).resolve()
    ).startswith(str(root)):
        return "editable"

    if "pipx" in parts:
        return "pipx"
    # uv keeps tool environments under <data>/uv/tools/<name>. Require both
    # segments: a plain venv created *by* uv is an ordinary venv, not a tool.
    if "uv" in parts and "tools" in parts:
        return "uv-tool"
    if (root / "pyvenv.cfg").exists():
        return "venv"
    return "unknown"


def channel_requirements() -> tuple[str, ...]:
    """Requirements for channels this install has enabled.

    Only enabled channels: reinstalling every discoverable channel's
    dependencies would pull packages the operator never asked for, and an upgrade
    is the wrong moment to widen an install's footprint.
    """
    try:
        from atom.channels.registry import discover_plugins
        from atom.config.loader import load_config
    except Exception as exc:  # pragma: no cover — import-time environment damage
        logger.debug("Could not resolve channel requirements: {}", exc)
        return ()

    try:
        config = load_config()
        plugins = discover_plugins()
    except Exception as exc:
        logger.debug("Could not read channel config: {}", exc)
        return ()

    requirements: list[str] = []
    for name, plugin in plugins.items():
        section = getattr(config.channels, name, None)
        if section is None:
            continue
        enabled = _section_enabled(section, default=plugin.default_enabled)
        if not enabled:
            continue
        requirements.extend(str(dep) for dep in getattr(plugin, "dependencies", ()))
    # Deduplicate while keeping order, so the printed command is stable.
    return tuple(dict.fromkeys(requirements))


def _section_enabled(section: Any, *, default: bool) -> bool:
    raw: object
    if isinstance(section, dict):
        raw = cast(dict[str, Any], section).get("enabled")
    else:
        raw = getattr(section, "enabled", None)
    return default if raw is None else bool(raw)


def plan_upgrade(
    *,
    method: InstallMethod | None = None,
    ref: str | None = None,
    repo: str = DEFAULT_REPO,
    include_channels: bool = True,
) -> UpgradePlan:
    """Resolve the upgrade command for this install without running it."""
    resolved = method or detect_install_method()
    extras = channel_requirements() if include_channels else ()

    if resolved == "uv-tool":
        if ref:
            # A pinned ref is a reinstall, not an upgrade: `uv tool upgrade`
            # takes no source, so asking for a tag has to go through install.
            command: tuple[str, ...] = (
                "uv", "tool", "install", "--force", f"git+{repo}@{ref}",
            )
        else:
            command = ("uv", "tool", "upgrade", "atom")
        for requirement in extras:
            command = (*command, "--with", requirement)
        return UpgradePlan(resolved, command, extras)

    if resolved == "pipx":
        target = f"git+{repo}@{ref}" if ref else "atom"
        command = (
            ("pipx", "install", "--force", target)
            if ref
            else ("pipx", "upgrade", "atom")
        )
        return UpgradePlan(resolved, command, extras)

    if resolved == "venv":
        target = f"git+{repo}@{ref}" if ref else f"git+{repo}"
        return UpgradePlan(
            resolved,
            (sys.executable, "-m", "pip", "install", "--upgrade", target),
            extras,
        )

    if resolved == "editable":
        return UpgradePlan(
            resolved,
            None,
            extras,
            refusal=(
                "This is a source checkout, so its version comes from git rather "
                "than a package index. Use git pull, then re-sync the environment."
            ),
        )

    return UpgradePlan(
        resolved,
        None,
        extras,
        refusal=(
            "Could not tell how atom was installed, and guessing risks running a "
            "command that uninstalls a working install. Upgrade it the way you "
            "installed it."
        ),
    )


def _installed_version(runner: Callable[..., Any], executable: str) -> str | None:
    """Read the version from a *fresh* process.

    Deliberately not ``atom.__version__``: this process imported that before the
    upgrade ran, so it reports the old number and every upgrade would look like a
    no-op.
    """
    try:
        proc = runner(
            [executable, "-c", "import atom; print(atom.__version__)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.debug("Could not read version after upgrade: {}", exc)
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def run_upgrade(
    plan: UpgradePlan,
    *,
    version_before: str,
    runner: Callable[..., Any] = subprocess.run,
    restart_service: bool = True,
    service_restarter: Callable[[], tuple[bool, str]] | None = None,
    dry_run: bool = False,
) -> UpgradeResult:
    """Execute *plan*, then restart the gateway so the new code is what runs."""
    if plan.command is None:
        return UpgradeResult(
            False,
            plan.method,
            plan.refusal or "This install cannot be upgraded in place.",
            version_before,
        )

    if shutil.which(plan.command[0]) is None and plan.command[0] != sys.executable:
        return UpgradeResult(
            False,
            plan.method,
            f"{plan.command[0]} is not on PATH, so the upgrade cannot run here.",
            version_before,
        )

    if dry_run:
        return UpgradeResult(
            True,
            plan.method,
            "Dry run; nothing was changed.",
            version_before,
            commands=[plan.command],
        )

    try:
        proc = runner(list(plan.command), capture_output=True, text=True, check=False)
    except OSError as exc:
        return UpgradeResult(
            False, plan.method, f"Upgrade command failed: {exc}", version_before,
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {proc.returncode}"
        return UpgradeResult(
            False,
            plan.method,
            f"Upgrade command failed: {tail}",
            version_before,
            commands=[plan.command],
        )

    version_after = _installed_version(runner, sys.executable)
    result = UpgradeResult(
        True,
        plan.method,
        "Upgraded." if version_after != version_before else "Already up to date.",
        version_before,
        version_after,
        commands=[plan.command],
    )

    # Restarting is the whole point: without it the files on disk are new and the
    # process serving traffic is not. Skipped when nothing changed, so a no-op
    # upgrade does not drop a live connection for nothing.
    if restart_service and result.changed:
        restarter = service_restarter or restart_gateway_service
        try:
            restarted, message = restarter()
        except Exception as exc:  # a failed restart must not fail the upgrade
            logger.warning("Gateway restart after upgrade failed: {}", exc)
            restarted, message = False, str(exc)
        result.service_restarted = restarted
        result.service_message = message

    return result


def restart_gateway_service(
    *,
    name: str = "atom-gateway",
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[bool, str]:
    """Restart the installed gateway service, if there is one.

    Returns ``(False, reason)`` when no service is installed — an ordinary
    outcome, not a failure: plenty of installs run the gateway in a terminal, and
    those pick up new code on their own next start.
    """
    if sys.platform == "darwin":
        return _restart_launchd(name=name, runner=runner, which=which)
    return _restart_systemd(name=name, runner=runner, which=which)


def _restart_systemd(
    *,
    name: str,
    runner: Callable[..., Any],
    which: Callable[[str], str | None],
) -> tuple[bool, str]:
    if which("systemctl") is None:
        return False, "systemctl is not available, so no service was restarted."

    unit = name if name.endswith(".service") else f"{name}.service"
    # Root has no login session on a headless host, so `--user` cannot connect to
    # a bus there; a non-root user's unit is the user one. Same rule the installer
    # uses, so the two agree about which unit they mean.
    geteuid = getattr(os, "geteuid", None)
    scope_flag: tuple[str, ...] = () if geteuid is not None and geteuid() == 0 else ("--user",)

    probe = runner(
        ["systemctl", *scope_flag, "is-active", unit],
        capture_output=True, text=True, check=False,
    )
    state = (probe.stdout or "").strip()
    if state != "active":
        return False, f"No running {unit} to restart (state: {state or 'not found'})."

    proc = runner(
        ["systemctl", *scope_flag, "restart", unit],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"Could not restart {unit}: {detail or proc.returncode}"
    return True, f"Restarted {unit}."


def _restart_launchd(
    *,
    name: str,
    runner: Callable[..., Any],
    which: Callable[[str], str | None],
) -> tuple[bool, str]:
    if which("launchctl") is None:
        return False, "launchctl is not available, so no service was restarted."

    label = name if name.startswith("ai.atom.") else f"ai.atom.{name}"
    geteuid = getattr(os, "geteuid", None)
    uid = geteuid() if geteuid is not None else 0
    target = f"gui/{uid}/{label}"

    probe = runner(
        ["launchctl", "print", target], capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        return False, f"No {label} agent is loaded, so nothing was restarted."

    proc = runner(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"Could not restart {label}: {detail or proc.returncode}"
    return True, f"Restarted {label}."
