"""Operator-declared secret values, stored outside the agent's reach.

Values live in ``~/.atom/private/secrets.env`` at mode 0600. The agent never
receives them: it learns the *names* from runtime context and references them as
shell variables, which ``ExecTool`` expands inside the subprocess.

The file is line-oriented rather than a dict round-trip so operator comments and
ordering survive a write from the ``/secrets`` command.
"""

from __future__ import annotations

import os
import re
import stat
import uuid
from contextlib import suppress
from pathlib import Path
from threading import RLock

from loguru import logger

from atom.config.paths import get_private_dir

SECRETS_FILENAME = "secrets.env"

#: POSIX environment variable naming, minus the lowercase forms. Values are
#: expanded as ``$NAME`` in a shell, so anything outside this set either fails
#: to expand or changes the meaning of the surrounding command.
NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

MAX_NAME_LENGTH = 64

#: Names that ``ExecTool`` sets itself, plus ``PATH`` and the loader variables.
#: A secret using one of these would either be dropped by injection precedence
#: or break the subprocess. ``PATH``, ``LD_PRELOAD`` and ``PYTHONPATH`` are the
#: dangerous ones: making them settable would let anyone with command access
#: redirect the binaries and libraries the agent invokes.
RESERVED_NAMES = frozenset({
    "ATOM_PATH_APPEND",
    "ATOM_PATH_PREPEND",
    "BASH_ENV",
    "HOME",
    "IFS",
    "LANG",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PATH",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUNBUFFERED",
    "SHELL",
    "TERM",
})


class SecretError(ValueError):
    """A secret name or value was rejected. The message never quotes a value."""


def normalize_name(name: str) -> str:
    """Uppercase and trim *name* so mobile input matches stored entries.

    Only one case can ever reach the store, so a later lookup or delete cannot
    be ambiguous between ``TOKEN`` and ``token``.
    """
    return name.strip().upper()


def validate_name(name: str) -> str:
    """Return the normalized form of *name*, or raise :class:`SecretError`."""
    normalized = normalize_name(name)
    if not normalized:
        raise SecretError("Secret name is required.")
    if len(normalized) > MAX_NAME_LENGTH:
        raise SecretError(f"Secret name must be at most {MAX_NAME_LENGTH} characters.")
    if not NAME_PATTERN.match(normalized):
        raise SecretError(
            "Secret name must use letters, digits and underscore only, "
            "and must not start with a digit."
        )
    if normalized in RESERVED_NAMES:
        raise SecretError(f"{normalized} is reserved and cannot be used as a secret name.")
    return normalized


def validate_value(value: str) -> str:
    """Return *value* unchanged, or raise :class:`SecretError`.

    Newlines are rejected rather than escaped: a value containing one would
    write a second assignment from a single command, which is an escalation
    path if any later feature grants meaning to a specific name. Rejecting is
    safer than quoting, which only moves the problem into a parser.
    """
    if value == "":
        raise SecretError("Secret value is required.")
    if "\n" in value or "\r" in value:
        raise SecretError("Secret value must not contain line breaks.")
    if "\x00" in value:
        raise SecretError("Secret value must not contain null bytes.")
    return value


def _quote(value: str) -> str:
    """Render *value* as a single-quoted assignment fragment."""
    return "'" + value.replace("'", "'\\''") + "'"


def _unquote(raw: str) -> str:
    """Reverse :func:`_quote` for the quoting styles a hand editor may produce."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        inner = text[1:-1]
        return inner.replace("'\\''", "'") if text[0] == "'" else inner
    return text


def _split_assignment(line: str) -> tuple[str, str] | None:
    """Parse ``NAME=value`` from *line*, or return None for blanks and comments.

    Lines whose name does not validate are treated as opaque: they are preserved
    on write but never injected, so a hand-edited lowercase entry does not
    silently become an environment variable.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    name, sep, raw_value = stripped.partition("=")
    if not sep:
        return None
    name = name.strip()
    if not NAME_PATTERN.match(name) or name in RESERVED_NAMES:
        return None
    return name, _unquote(raw_value)


def _write_secret_file(path: Path, lines: list[str]) -> None:
    """Write *lines* to ``secrets.env`` atomically, ending with one newline."""
    content = "\n".join(lines).rstrip("\n")
    write_private_text(path, content + "\n" if content else "")


def write_private_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically at mode 0600.

    ``utils.helpers._write_text_atomic`` cannot be reused for files under
    ``private/``: it opens the temp file at the process umask and only chmods
    afterwards, which would leave the contents briefly world-readable. This
    opens with 0600 from the start.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        with suppress(OSError):
            path.chmod(0o600)
        # fsync the directory so the rename survives a crash, matching the
        # durability of agent/memory.py's history writes.
        with suppress(OSError, NotImplementedError):
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class SecretStore:
    """Read/write access to ``secrets.env``.

    Every mutation re-reads the file, edits the parsed lines and writes the
    whole file atomically, so an operator's comments and ordering survive.

    The lock serializes atom's own concurrent access. A separate process (the
    CLI running while the gateway is up) is last-write-wins; the write itself is
    still atomic, so the file is never torn.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path
        self._lock = RLock()

    @property
    def path(self) -> Path:
        """Path to the secrets file, creating the private directory if needed."""
        if self._explicit_path is not None:
            return self._explicit_path
        return get_private_dir() / SECRETS_FILENAME

    @property
    def read_path(self) -> Path:
        """Path for reads, without creating anything.

        ``load`` runs on hot paths such as building a subprocess environment, so
        it must not create a directory as a side effect, and must not raise when
        the parent is unwritable.
        """
        if self._explicit_path is not None:
            return self._explicit_path
        return get_private_dir(create=False) / SECRETS_FILENAME

    # -- reading ---------------------------------------------------------

    def _read_lines(self) -> list[str]:
        path = self.read_path
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning("Could not read secrets file: {}", exc)
            return []
        self._warn_on_loose_mode(path)
        return content.splitlines()

    @staticmethod
    def _warn_on_loose_mode(path: Path) -> None:
        """Warn when the file is group- or world-readable.

        Not an error: an operator may have deliberately relaxed it, and
        refusing to load would break a working setup at startup.
        """
        with suppress(OSError):
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                logger.warning(
                    "Secrets file {} is readable beyond its owner (mode {:o}); "
                    "tighten it with chmod 600.",
                    path,
                    mode,
                )

    def load(self) -> dict[str, str]:
        """Return all stored secrets as ``name -> value``.

        Later assignments win, matching how a shell sources a file.
        """
        with self._lock:
            values: dict[str, str] = {}
            for line in self._read_lines():
                parsed = _split_assignment(line)
                if parsed is not None:
                    values[parsed[0]] = parsed[1]
            return values

    def names(self) -> list[str]:
        """Return stored secret names, sorted. Safe to show the agent."""
        return sorted(self.load())

    def get(self, name: str) -> str | None:
        """Return the value for *name*, or None. Never expose the result to a prompt."""
        return self.load().get(normalize_name(name))

    # -- mutation --------------------------------------------------------

    def set(self, name: str, value: str) -> str:
        """Store *value* under *name*, replacing any existing entry.

        Returns the normalized name. Raises :class:`SecretError` on invalid
        input, with a message that never quotes the value.
        """
        key = validate_name(name)
        validated = validate_value(value)
        with self._lock:
            lines = self._read_lines()
            replacement = f"{key}={_quote(validated)}"
            updated: list[str] = []
            replaced = False
            for line in lines:
                parsed = _split_assignment(line)
                if parsed is not None and parsed[0] == key:
                    # Keep the first occurrence's position, drop later duplicates
                    # so the file cannot disagree with what injection uses.
                    if not replaced:
                        updated.append(replacement)
                        replaced = True
                    continue
                updated.append(line)
            if not replaced:
                updated.append(replacement)
            _write_secret_file(self.path, updated)
        return key

    def delete(self, name: str) -> str | None:
        """Remove *name*. Returns the normalized name, or None if absent."""
        key = validate_name(name)
        with self._lock:
            lines = self._read_lines()
            kept: list[str] = []
            found = False
            for line in lines:
                parsed = _split_assignment(line)
                if parsed is not None and parsed[0] == key:
                    found = True
                    continue
                kept.append(line)
            if not found:
                return None
            _write_secret_file(self.path, kept)
        return key


#: Process-wide store. The file is the source of truth and is re-read on every
#: access, so this holds no cached values.
DEFAULT_SECRET_STORE = SecretStore()
