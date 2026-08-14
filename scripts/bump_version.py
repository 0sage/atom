"""Bump atom's version in pyproject.toml, one component at a time.

`pyproject.toml` is the single source of truth: `atom.__version__` reads
installed dist metadata and falls back to this file, so nothing else needs
editing. See `.agent/versioning.md` for which component to pick.

    python -m scripts.bump_version patch     # 0.3.0 -> 0.3.1
    python -m scripts.bump_version minor     # 0.3.1 -> 0.4.0
    python -m scripts.bump_version major     # 0.4.0 -> 1.0.0
    python -m scripts.bump_version --check    # validate without writing
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal, NamedTuple, cast

Component = Literal["major", "minor", "patch"]
COMPONENTS: tuple[Component, ...] = ("major", "minor", "patch")

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Anchored to the [project] table's own `version` key. The value is matched
# loosely so a malformed version reaches _parse for a real error message
# instead of silently failing to match here.
_VERSION_LINE = re.compile(
    r'(?m)^(?P<prefix>version\s*=\s*")(?P<value>[^"]*)(?P<suffix>")'
)


class Version(NamedTuple):
    """A three-component release version. No pre-release or local segments."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def bumped(self, component: Component) -> Version:
        """Increment *component* and reset everything below it to zero."""
        if component == "major":
            return Version(self.major + 1, 0, 0)
        if component == "minor":
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)


class VersionError(RuntimeError):
    """A version could not be parsed, or a bump would not be a single step."""


def parse_version(raw: str) -> Version:
    """Parse `X.Y.Z` into a Version, rejecting anything else."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", raw.strip())
    if match is None:
        raise VersionError(
            f"{raw!r} is not a bare X.Y.Z version. atom does not ship "
            f"pre-release, post-release, or local version segments -- they make "
            f"`atom --version` ambiguous in bug reports."
        )
    major, minor, patch = (int(g) for g in match.groups())
    return Version(major, minor, patch)


def read_version() -> Version:
    """Read the declared version from pyproject.toml."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = cast(dict[str, object], data.get("project", {}))
    raw = project.get("version")
    if not isinstance(raw, str):
        raise VersionError("pyproject.toml has no [project] version string")
    return parse_version(raw)


def write_version(new: Version) -> None:
    """Rewrite the version in place, preserving the rest of the file byte for byte."""
    text = PYPROJECT.read_text(encoding="utf-8")
    replaced, count = _VERSION_LINE.subn(
        lambda m: f"{m.group('prefix')}{new}{m.group('suffix')}", text, count=1
    )
    if count != 1:
        raise VersionError("could not locate the version line in pyproject.toml")
    PYPROJECT.write_text(replaced, encoding="utf-8")


def verify_step(old: Version, new: Version) -> None:
    """Reject anything that is not exactly one component's single increment.

    Guards against the two ways a version drifts in practice: skipping ahead
    (0.3.0 -> 0.5.0) hides a release that never existed, and moving backwards
    makes an older artifact outrank a newer one.
    """
    for component in COMPONENTS:
        if new == old.bumped(component):
            return
    raise VersionError(
        f"{old} -> {new} is not a single-component bump. Expected one of: "
        + ", ".join(f"{old.bumped(c)} ({c})" for c in COMPONENTS)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.bump_version",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "component",
        nargs="?",
        choices=COMPONENTS,
        help="which component to increment; see .agent/versioning.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the declared version and print it, without writing",
    )
    args = parser.parse_args(argv)
    component = cast("Component | None", args.component)
    check_only = cast(bool, args.check)

    try:
        current = read_version()
        if check_only:
            print(current)
            return 0
        if component is None:
            parser.error("pick a component, or pass --check")
        new = current.bumped(component)
        verify_step(current, new)
        write_version(new)
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{current} -> {new}")
    print(f"next: git commit, then git tag -a v{new} -m 'atom v{new}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
