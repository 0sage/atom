"""Inject stored secrets into a subprocess environment.

The agent never receives a value in a prompt or tool argument: it writes
``$NAME`` and the shell expands it inside the child process. A command that
prints its own environment will still surface the value in its output — see
``.agent/privacy.md`` for why that is accepted rather than scrubbed.
"""

from __future__ import annotations

from atom.privacy import store as store_module
from atom.privacy.store import SecretStore


def secret_env(store: SecretStore | None = None) -> dict[str, str]:
    """Return stored secrets as environment variables."""
    resolved = store or store_module.DEFAULT_SECRET_STORE
    return resolved.load()


def inject_secrets(env: dict[str, str], store: SecretStore | None = None) -> dict[str, str]:
    """Add stored secrets to *env* in place, without overriding existing keys.

    Existing keys win. ``store.validate_name`` already rejects the names
    ``ExecTool`` sets itself, and ``store.load`` drops them from a hand-edited
    file, so a collision should be impossible — but precedence is the cheaper
    of the two guards and it is the one that holds if the reserved list and the
    base environment ever drift apart.
    """
    for name, value in secret_env(store).items():
        if name not in env:
            env[name] = value
    return env


def secret_names(store: SecretStore | None = None) -> list[str]:
    """Return stored names, sorted. Safe to show the agent."""
    return sorted(secret_env(store))
