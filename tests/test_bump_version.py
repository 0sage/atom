"""Tests for the version bump script (scripts/bump_version.py)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import bump_version
from scripts.bump_version import Version, VersionError, parse_version, verify_step


class TestParseVersion:
    def test_parses_bare_three_component_version(self) -> None:
        assert parse_version("1.2.3") == Version(1, 2, 3)

    def test_tolerates_surrounding_whitespace(self) -> None:
        assert parse_version("  0.3.0 \n") == Version(0, 3, 0)

    def test_accepts_multi_digit_components(self) -> None:
        assert parse_version("10.20.30") == Version(10, 20, 30)

    @pytest.mark.parametrize(
        "raw",
        [
            "1.2",  # too few components
            "1.2.3.4",  # too many
            "1.2.3rc1",  # pre-release
            "1.2.3.post1",  # post-release
            "1.2.3+local",  # local segment
            "v1.2.3",  # tag form, not the version itself
            "1.2.-3",  # negative
            "",
        ],
    )
    def test_rejects_anything_but_x_y_z(self, raw: str) -> None:
        """Non-bare versions make `atom --version` ambiguous in bug reports."""
        with pytest.raises(VersionError):
            parse_version(raw)


class TestBumped:
    def test_patch_increments_last_component_only(self) -> None:
        assert Version(0, 3, 0).bumped("patch") == Version(0, 3, 1)

    def test_minor_resets_patch(self) -> None:
        assert Version(0, 3, 7).bumped("minor") == Version(0, 4, 0)

    def test_major_resets_minor_and_patch(self) -> None:
        assert Version(0, 4, 9).bumped("major") == Version(1, 0, 0)

    def test_str_roundtrips_through_parse(self) -> None:
        v = Version(2, 11, 4)
        assert parse_version(str(v)) == v


class TestVerifyStep:
    @pytest.mark.parametrize("component", ["major", "minor", "patch"])
    def test_accepts_each_single_component_bump(self, component: str) -> None:
        old = Version(1, 2, 3)
        verify_step(old, old.bumped(component))  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize(
        "new",
        [
            Version(1, 2, 5),  # skipped a patch
            Version(1, 4, 0),  # skipped a minor
            Version(3, 0, 0),  # skipped a major
            Version(2, 2, 3),  # major bumped without resetting below
            Version(1, 3, 3),  # minor bumped without resetting patch
            Version(1, 2, 3),  # no change at all
            Version(1, 2, 2),  # backwards
            Version(0, 9, 9),  # backwards across major
        ],
    )
    def test_rejects_jumps_and_reversals(self, new: Version) -> None:
        """Skipping implies a release that never shipped; going back inverts order."""
        with pytest.raises(VersionError):
            verify_step(Version(1, 2, 3), new)

    def test_error_names_the_three_valid_next_versions(self) -> None:
        with pytest.raises(VersionError) as exc:
            verify_step(Version(0, 3, 0), Version(9, 9, 9))
        message = str(exc.value)
        assert "1.0.0" in message and "0.4.0" in message and "0.3.1" in message


class TestReadWrite:
    """These touch pyproject.toml, so each test points the module at a temp copy."""

    @staticmethod
    def _use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
        target = tmp_path / "pyproject.toml"
        target.write_text(body, encoding="utf-8")
        monkeypatch.setattr(bump_version, "PYPROJECT", target)
        return target

    def test_reads_declared_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use(tmp_path, monkeypatch, '[project]\nname = "atom"\nversion = "0.3.0"\n')
        assert bump_version.read_version() == Version(0, 3, 0)

    def test_read_rejects_missing_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use(tmp_path, monkeypatch, '[project]\nname = "atom"\n')
        with pytest.raises(VersionError):
            bump_version.read_version()

    def test_write_preserves_the_rest_of_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the version's own characters change -- comments and layout survive."""
        body = (
            "[project]\n"
            'name = "atom"\n'
            'version = "0.3.0"\n'
            '# a comment mentioning 0.3.0 that must not move\n'
            'description = "x"\n'
            "\n"
            "[tool.ruff]\n"
            "line-length = 100\n"
        )
        target = self._use(tmp_path, monkeypatch, body)
        bump_version.write_version(Version(0, 3, 1))
        assert target.read_text(encoding="utf-8") == body.replace(
            'version = "0.3.0"', 'version = "0.3.1"'
        )

    def test_write_only_touches_the_project_version_not_a_later_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `version` key in a later table must be left alone."""
        target = self._use(
            tmp_path,
            monkeypatch,
            '[project]\nversion = "0.3.0"\n\n[tool.other]\nversion = "9.9.9"\n',
        )
        bump_version.write_version(Version(0, 4, 0))
        text = target.read_text(encoding="utf-8")
        assert 'version = "0.4.0"' in text
        assert 'version = "9.9.9"' in text


class TestMain:
    @staticmethod
    def _use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str) -> Path:
        target = tmp_path / "pyproject.toml"
        target.write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
        monkeypatch.setattr(bump_version, "PYPROJECT", target)
        return target

    def test_check_prints_version_and_writes_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = self._use(tmp_path, monkeypatch, "0.3.0")
        before = target.read_text(encoding="utf-8")

        assert bump_version.main(["--check"]) == 0

        assert capsys.readouterr().out.strip() == "0.3.0"
        assert target.read_text(encoding="utf-8") == before

    def test_bump_writes_and_reports_the_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = self._use(tmp_path, monkeypatch, "0.3.0")

        assert bump_version.main(["minor"]) == 0

        assert 'version = "0.4.0"' in target.read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "0.3.0 -> 0.4.0" in out
        assert "v0.4.0" in out  # reminds the caller to tag

    def test_missing_component_exits_without_printing_a_version(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """argparse exits 2; the version must not be printed as if it succeeded."""
        self._use(tmp_path, monkeypatch, "0.3.0")

        with pytest.raises(SystemExit) as exc:
            bump_version.main([])

        assert exc.value.code == 2
        assert capsys.readouterr().out == ""

    def test_malformed_declared_version_fails_cleanly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = self._use(tmp_path, monkeypatch, "0.3.0rc1")
        before = target.read_text(encoding="utf-8")

        assert bump_version.main(["patch"]) == 1

        assert "not a bare X.Y.Z version" in capsys.readouterr().err
        assert target.read_text(encoding="utf-8") == before


def test_repo_version_satisfies_the_rules_the_script_enforces() -> None:
    """atom's own declared version must be a bare X.Y.Z the script can bump.

    Guards against a hand-edit landing something like `0.4.0rc1`, which would
    only be caught the next time somebody tried to release.
    """
    declared = tomllib.loads(bump_version.PYPROJECT.read_text(encoding="utf-8"))
    raw = declared["project"]["version"]

    assert parse_version(raw) == bump_version.read_version()
