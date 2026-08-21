"""Credential storage for the OpenAI Codex OAuth provider.

Tokens live at ``<data dir>/auth/codex.json`` beside the MCP credential store
rather than in ``oauth-cli-kit``'s ``platformdirs`` location, so an atom install
keeps every secret under its own instance directory.

``oauth-cli-kit`` owns the OAuth protocol itself: PKCE, the local callback
server, locked refresh with a stale re-read, and importing an existing
``~/.codex/auth.json`` written by the Codex CLI. This module supplies only the
storage backend and the atomic, owner-only write behavior that the kit's plain
``write_text`` does not provide.
"""

# oauth-cli-kit does not publish type stubs.
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import base64
import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
from oauth_cli_kit.storage import FileTokenStorage

from atom.utils.helpers import _write_text_atomic  # pyright: ignore[reportPrivateUsage]

_TOKEN_FILENAME = "codex.json"
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Refresh this far ahead of expiry. The kit defaults to 60s; a turn can outlive
# that once tool calls are in play, so ask for a wider margin.
MIN_TOKEN_TTL_SECONDS = 300


def _access_token_expiry_ms(access_token: str) -> int | None:
    """Read ``exp`` out of a JWT access token, in epoch milliseconds.

    Signature verification is deliberately absent: the token is not being
    trusted here, only asked how long its issuer says it lives. The backend
    remains the authority — a wrong answer costs a refresh, not access.
    """
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = cast(dict[str, Any], json.loads(base64.urlsafe_b64decode(payload)))
    except (IndexError, ValueError, TypeError):
        return None
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return int(exp * 1000)


def codex_token_path() -> Path:
    """Return the on-disk location of the Codex OAuth token."""
    from atom.config.paths import get_data_dir

    return get_data_dir() / "auth" / _TOKEN_FILENAME


class CodexTokenStorage(FileTokenStorage):
    """Kit storage backend rooted in atom's data dir with hardened writes.

    ``import_codex_cli`` stays enabled: when atom has no token of its own, the
    kit imports one from ``~/.codex/auth.json``. That is also the recovery path
    when a refresh fails because OAuth refresh tokens are single-use and the
    Codex CLI already consumed the current one.
    """

    def __init__(self) -> None:
        super().__init__(
            token_filename=_TOKEN_FILENAME,
            app_name="atom",
            import_codex_cli=True,
        )

    def get_token_path(self) -> Path:
        return codex_token_path()

    def load(self) -> OAuthToken | None:
        path = self.get_token_path()
        existed = path.exists()
        token = super().load()
        if token is None:
            return None

        if not existed and path.exists():
            # This token came from ~/.codex/auth.json. The kit dates such an
            # import as mtime + 1h, which reads as long expired for a file the
            # Codex CLI wrote hours ago — and that would trigger an immediate
            # refresh. OpenAI refresh tokens are single-use, so that refresh
            # would invalidate the CLI's own credential as a side effect of
            # atom merely starting up. Trust the JWT's own ``exp`` instead.
            claimed = _access_token_expiry_ms(token.access)
            if claimed is not None and claimed > token.expires:
                token = OAuthToken(
                    access=token.access,
                    refresh=token.refresh,
                    expires=claimed,
                    account_id=token.account_id,
                )
            # The import was written through the kit's plain ``write_text``;
            # re-save so its permissions match every other write.
            self.save(token)
        return token

    def save(self, token: OAuthToken) -> None:
        path = self.get_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(path.parent, _DIR_MODE)

        payload: dict[str, Any] = {
            "access": token.access,
            "refresh": token.refresh,
            "expires": token.expires,
        }
        if token.account_id:
            payload["account_id"] = token.account_id

        # Pre-create with the restrictive mode so the atomic replace inherits it
        # rather than briefly exposing a token at the process umask.
        if not path.exists():
            with suppress(OSError):
                path.touch(mode=_FILE_MODE)
        _write_text_atomic(path, json.dumps(payload, ensure_ascii=True, indent=2))
        with suppress(OSError):
            os.chmod(path, _FILE_MODE)


def get_codex_token(proxy: str | None = None) -> OAuthToken:
    """Return a usable Codex access token, refreshing it when near expiry.

    Raises ``RuntimeError`` when no credential exists — the caller should point
    the user at ``atom auth login openai-codex``.
    """
    from oauth_cli_kit import get_token

    return get_token(
        OPENAI_CODEX_PROVIDER,
        storage=CodexTokenStorage(),
        min_ttl_seconds=MIN_TOKEN_TTL_SECONDS,
        proxy=proxy,
    )


def _codex_cli_credential_path() -> Path:
    """Return the Codex CLI's own credential location."""
    return Path.home() / ".codex" / "auth.json"


def codex_cli_credential_available() -> bool:
    """Whether the Codex CLI holds a credential atom could import.

    Read-only on purpose: reporting status must not import anything, since an
    import writes atom's own store and would change what it is reporting on.
    """
    path = _codex_cli_credential_path()
    try:
        raw = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return False
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        return False
    fields = cast(dict[str, Any], tokens)
    return all(
        bool(fields.get(key)) for key in ("access_token", "refresh_token", "account_id")
    )


def codex_token_status() -> dict[str, Any]:
    """Describe the stored credential without exposing any token material."""
    path = codex_token_path()
    if not path.exists():
        # atom has no store of its own yet, but the provider would import the
        # Codex CLI's credential on first use — so this counts as signed in.
        if codex_cli_credential_available():
            return {
                "configured": True,
                "valid": True,
                "path": str(path),
                "source": "codex_cli",
                "cli_path": str(_codex_cli_credential_path()),
            }
        return {"configured": False, "path": str(path)}

    status: dict[str, Any] = {"configured": True, "path": str(path), "source": "atom"}
    with suppress(OSError):
        status["mode"] = stat.S_IMODE(path.stat().st_mode)
    try:
        raw = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        status["valid"] = False
        return status

    expires = raw.get("expires")
    status["valid"] = bool(raw.get("access")) and bool(raw.get("refresh"))
    status["has_account_id"] = bool(raw.get("account_id"))
    if isinstance(expires, int) and not isinstance(expires, bool):
        status["expires_ms"] = expires
    return status


def clear_codex_token() -> bool:
    """Delete the stored credential. Returns whether a file was removed."""
    path = codex_token_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    with suppress(OSError):
        path.with_suffix(".lock").unlink(missing_ok=True)
    return True
