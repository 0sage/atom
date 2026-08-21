"""Tests for Codex OAuth credential storage.

The OAuth protocol itself belongs to ``oauth-cli-kit``. What is tested here is
atom's storage contract: where the token lives, that it is written atomically
with owner-only permissions, and that no token material leaks into reported
status.
"""

import base64
import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import patch

from oauth_cli_kit.models import OAuthToken

from atom.config.loader import set_config_path
from atom.providers.openai_codex_auth import (
    MIN_TOKEN_TTL_SECONDS,
    CodexTokenStorage,
    clear_codex_token,
    codex_cli_credential_available,
    codex_token_path,
    codex_token_status,
    get_codex_token,
)

_ACCESS = "access-token-secret"
_REFRESH = "refresh-token-secret"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwt(exp: int) -> str:
    """Build an unsigned JWT carrying only the ``exp`` claim we read."""
    return ".".join([
        _b64(json.dumps({"alg": "none"}).encode()),
        _b64(json.dumps({"exp": exp}).encode()),
        "signature",
    ])


def _write_cli_auth(tmp_path: Path, *, access: str) -> Path:
    """Write a ~/.codex/auth.json under a fake home, as the Codex CLI would."""
    codex_home = tmp_path / "fake-home" / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    path = codex_home / "auth.json"
    path.write_text(
        json.dumps({
            "tokens": {
                "access_token": access,
                "refresh_token": _REFRESH,
                "account_id": "acct_cli",
            }
        })
    )
    return path


def _token(expires: int = 1_900_000_000_000) -> OAuthToken:
    return OAuthToken(
        access=_ACCESS,
        refresh=_REFRESH,
        expires=expires,
        account_id="acct_1",
    )


# ======================================================================
# Storage location
# ======================================================================


class TestTokenPath:
    def test_lives_under_the_instance_auth_dir(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        assert codex_token_path() == tmp_path / "auth" / "codex.json"

    def test_storage_agrees_with_the_module_helper(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        assert CodexTokenStorage().get_token_path() == codex_token_path()

    def test_not_placed_in_the_codex_cli_directory(self, tmp_path: Path):
        """atom never writes to ~/.codex; that file belongs to the Codex CLI."""
        set_config_path(tmp_path / "config.json")
        assert ".codex" not in str(codex_token_path())


# ======================================================================
# Write hardening
# ======================================================================


class TestSaveHardening:
    def test_round_trips_every_field(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        storage = CodexTokenStorage()
        storage.save(_token())

        loaded = storage.load()
        assert loaded is not None
        assert loaded.access == _ACCESS
        assert loaded.refresh == _REFRESH
        assert loaded.account_id == "acct_1"

    def test_file_is_owner_only(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())

        mode = stat.S_IMODE(codex_token_path().stat().st_mode)
        assert mode == 0o600
        assert not mode & 0o077

    def test_directory_is_owner_only(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())

        mode = stat.S_IMODE(codex_token_path().parent.stat().st_mode)
        assert mode == 0o700

    def test_overwrite_keeps_restrictive_mode(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        storage = CodexTokenStorage()
        storage.save(_token())
        storage.save(_token(expires=1_900_000_001_000))

        assert stat.S_IMODE(codex_token_path().stat().st_mode) == 0o600

    def test_no_temp_files_are_left_behind(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())

        leftovers = [p.name for p in codex_token_path().parent.iterdir() if ".tmp" in p.name]
        assert leftovers == []

    def test_account_id_is_omitted_when_absent(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(
            OAuthToken(access=_ACCESS, refresh=_REFRESH, expires=1, account_id=None)
        )

        payload = json.loads(codex_token_path().read_text())
        assert "account_id" not in payload


# ======================================================================
# Codex CLI import — the single-use refresh-token recovery path
# ======================================================================


class TestCodexCliImport:
    def test_import_is_enabled(self, tmp_path: Path):
        """A refresh consumed by the Codex CLI is recoverable from its auth.json."""
        set_config_path(tmp_path / "config.json")
        storage = CodexTokenStorage()
        assert storage._import_codex_cli is True  # pyright: ignore[reportPrivateUsage]

    def test_imported_token_is_rewritten_with_hardened_permissions(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        codex_home = tmp_path / "fake-home" / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "auth.json").write_text(
            json.dumps({
                "tokens": {
                    "access_token": _ACCESS,
                    "refresh_token": _REFRESH,
                    "account_id": "acct_cli",
                }
            })
        )

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            loaded = CodexTokenStorage().load()

        assert loaded is not None
        assert loaded.account_id == "acct_cli"
        assert stat.S_IMODE(codex_token_path().stat().st_mode) == 0o600

    def test_missing_cli_credential_returns_none(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        with patch("pathlib.Path.home", return_value=tmp_path / "empty-home"):
            assert CodexTokenStorage().load() is None

    def test_import_does_not_burn_the_cli_refresh_token(self, tmp_path: Path):
        """The kit dates an import as mtime+1h, which reads as long expired.

        Acting on that would refresh immediately, and because OpenAI refresh
        tokens are single-use it would invalidate the Codex CLI's own credential
        as a side effect of atom merely starting up. The JWT's ``exp`` wins.
        """
        set_config_path(tmp_path / "config.json")
        expires_at = int(time.time()) + 9 * 24 * 3600
        _write_cli_auth(tmp_path, access=_jwt(expires_at))

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            loaded = CodexTokenStorage().load()

        assert loaded is not None
        assert loaded.expires == expires_at * 1000
        # Far outside the refresh margin, so get_token returns it untouched.
        assert loaded.expires - int(time.time() * 1000) > MIN_TOKEN_TTL_SECONDS * 1000

    def test_import_of_a_genuinely_expired_token_still_refreshes(self, tmp_path: Path):
        """An old file whose JWT has also lapsed must still look expired."""
        set_config_path(tmp_path / "config.json")
        expired_at = int(time.time()) - 3600
        path = _write_cli_auth(tmp_path, access=_jwt(expired_at))
        stale = time.time() - 30 * 3600
        os.utime(path, (stale, stale))

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            loaded = CodexTokenStorage().load()

        assert loaded is not None
        assert loaded.expires == expired_at * 1000
        assert loaded.expires < int(time.time() * 1000)

    def test_opaque_access_token_falls_back_to_the_kit_estimate(self, tmp_path: Path):
        """A non-JWT token carries no exp; the kit's mtime estimate stands."""
        set_config_path(tmp_path / "config.json")
        _write_cli_auth(tmp_path, access="opaque-not-a-jwt")

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            loaded = CodexTokenStorage().load()

        assert loaded is not None
        assert loaded.access == "opaque-not-a-jwt"

    def test_jwt_expiry_is_never_used_to_shorten_a_token(self, tmp_path: Path):
        """Only a longer claimed life is adopted, so this cannot cause churn."""
        set_config_path(tmp_path / "config.json")
        _write_cli_auth(tmp_path, access=_jwt(int(time.time()) - 10_000))

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            loaded = CodexTokenStorage().load()

        assert loaded is not None
        cli_estimate_ms = int(
            (tmp_path / "fake-home" / ".codex" / "auth.json").stat().st_mtime * 1000
            + 3600 * 1000
        )
        assert loaded.expires == cli_estimate_ms

    def test_reload_of_an_atom_owned_token_does_not_rewrite_expiry(self, tmp_path: Path):
        """The JWT override applies to imports only, not to atom's own store."""
        set_config_path(tmp_path / "config.json")
        storage = CodexTokenStorage()
        storage.save(_token(expires=1_700_000_000_000))

        loaded = storage.load()
        assert loaded is not None
        assert loaded.expires == 1_700_000_000_000


# ======================================================================
# Refresh margin
# ======================================================================


class TestRefreshMargin:
    def test_margin_is_wider_than_the_library_default(self):
        """A turn with tool calls can outlive the kit's 60s default."""
        assert MIN_TOKEN_TTL_SECONDS >= 300

    def test_get_token_passes_atom_storage_and_margin(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")

        with patch("oauth_cli_kit.get_token", return_value=_token()) as mocked:
            get_codex_token(proxy="http://proxy.local:8080")

        _, kwargs = mocked.call_args
        assert kwargs["min_ttl_seconds"] == MIN_TOKEN_TTL_SECONDS
        assert kwargs["proxy"] == "http://proxy.local:8080"
        assert isinstance(kwargs["storage"], CodexTokenStorage)


# ======================================================================
# Status reporting
# ======================================================================


class TestStatus:
    def test_reports_unconfigured_when_absent(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        with patch("pathlib.Path.home", return_value=tmp_path / "empty-home"):
            status = codex_token_status()
        assert status["configured"] is False

    def test_importable_cli_credential_counts_as_signed_in(self, tmp_path: Path):
        """Status must match what the provider would actually do on first use."""
        set_config_path(tmp_path / "config.json")
        _write_cli_auth(tmp_path, access=_jwt(int(time.time()) + 3600))

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            status = codex_token_status()

        assert status["configured"] is True
        assert status["valid"] is True
        assert status["source"] == "codex_cli"

    def test_reporting_status_never_imports(self, tmp_path: Path):
        """Reading status must not write atom's store — that would change it."""
        set_config_path(tmp_path / "config.json")
        _write_cli_auth(tmp_path, access=_jwt(int(time.time()) + 3600))

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            codex_token_status()

        assert not codex_token_path().exists()

    def test_atom_owned_credential_is_labelled_as_such(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())
        assert codex_token_status()["source"] == "atom"

    def test_incomplete_cli_credential_is_not_counted(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        codex_home = tmp_path / "fake-home" / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": _ACCESS}})
        )

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            assert codex_cli_credential_available() is False
            assert codex_token_status()["configured"] is False

    def test_corrupt_cli_credential_is_not_counted(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        codex_home = tmp_path / "fake-home" / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "auth.json").write_text("{not json")

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            assert codex_cli_credential_available() is False

    def test_reports_configured_details(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())

        status = codex_token_status()
        assert status["configured"] is True
        assert status["valid"] is True
        assert status["has_account_id"] is True
        assert status["expires_ms"] == 1_900_000_000_000
        assert status["mode"] == 0o600

    def test_never_exposes_token_material(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())

        rendered = json.dumps(codex_token_status())
        assert _ACCESS not in rendered
        assert _REFRESH not in rendered

    def test_corrupt_file_is_reported_as_invalid(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        path = codex_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")

        status = codex_token_status()
        assert status["configured"] is True
        assert status["valid"] is False

    def test_incomplete_file_is_reported_as_invalid(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        path = codex_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"access": _ACCESS}))

        assert codex_token_status()["valid"] is False

    def test_non_integer_expiry_is_dropped(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        path = codex_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"access": _ACCESS, "refresh": _REFRESH, "expires": "soon"})
        )

        assert "expires_ms" not in codex_token_status()


# ======================================================================
# Logout
# ======================================================================


class TestClearToken:
    def test_removes_the_credential_and_its_lock(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())
        lock = codex_token_path().with_suffix(".lock")
        lock.touch()

        assert clear_codex_token() is True
        assert not codex_token_path().exists()
        assert not lock.exists()

    def test_is_idempotent(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        assert clear_codex_token() is False

    def test_leaves_the_codex_cli_credential_alone(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        codex_home = tmp_path / "fake-home" / ".codex"
        codex_home.mkdir(parents=True)
        cli_auth = codex_home / "auth.json"
        cli_auth.write_text("{}")
        CodexTokenStorage().save(_token())

        with patch("pathlib.Path.home", return_value=tmp_path / "fake-home"):
            clear_codex_token()

        assert cli_auth.exists()

    def test_unreadable_directory_reports_failure(self, tmp_path: Path):
        set_config_path(tmp_path / "config.json")
        CodexTokenStorage().save(_token())
        auth_dir = codex_token_path().parent
        os.chmod(auth_dir, 0o500)
        try:
            assert clear_codex_token() is False
        finally:
            os.chmod(auth_dir, 0o700)
