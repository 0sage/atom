"""``/secrets`` command handling.

Dispatched on the router's priority tier so neither the command text nor the
reply is written to session history. Replies never echo a value, including on
error: the reply travels back through the chat channel, so quoting the input
would undo the point of the command.
"""

from __future__ import annotations

from atom.privacy import store as store_module
from atom.privacy.store import SecretError, SecretStore

USAGE = (
    "Usage:\n"
    "/secrets — list stored names\n"
    "/secrets set NAME=value — store or replace\n"
    "/secrets del NAME — remove\n\n"
    "Names are uppercased automatically. Values are never shown back and are "
    "never sent to the model; the agent uses them as $NAME in shell commands."
)

def _describe(name: str, value: str) -> str:
    """Render one inventory line: enough to identify, not enough to use."""
    return f"- {name} ({len(value)} chars)"


def handle_secrets_command(args: str, store: SecretStore | None = None) -> str:
    """Handle ``/secrets`` and return the reply text.

    *args* is passed verbatim from the router, without case folding, because a
    value's case is significant.
    """
    # Resolved through the module rather than imported at load time so the
    # default store stays swappable — a binding captured at import cannot be
    # redirected, and a test that silently writes the real ~/.atom/secrets.env
    # is the one failure mode this feature must not have.
    store = store or store_module.DEFAULT_SECRET_STORE
    text = args.strip()

    if not text or text.lower() == "list":
        return _list(store)

    verb, _, rest = text.partition(" ")
    verb = verb.lower()
    rest = rest.strip()

    if verb == "set":
        return _set(store, rest)
    if verb in {"del", "delete", "rm", "remove", "unset"}:
        return _delete(store, rest)
    if verb in {"help", "-h", "--help"}:
        return USAGE
    if verb == "get":
        return (
            "Secret values are never shown. Use /secrets to list names, or "
            "reference the secret as $NAME in a shell command."
        )
    # Do not echo the unrecognized verb: a mistyped "set" would print the value.
    return f"Unknown /secrets subcommand.\n\n{USAGE}"


def _list(store: SecretStore) -> str:
    try:
        values = store.load()
    except OSError as exc:
        return f"Could not read the secrets file: {exc}"
    if not values:
        return f"No secrets stored.\n\n{USAGE}"
    lines = [f"Stored secrets ({len(values)}):"]
    lines.extend(_describe(name, values[name]) for name in sorted(values))
    lines.append("")
    lines.append("Values are not shown. The agent uses them as $NAME in shell commands.")
    return "\n".join(lines)


def _set(store: SecretStore, rest: str) -> str:
    if not rest:
        return f"Usage: /secrets set NAME=value\n\n{USAGE}"
    name, sep, value = rest.partition("=")
    if not sep:
        # Accept "set NAME value" as well; the value may itself contain spaces.
        name, _, value = rest.partition(" ")
    if not value:
        return f"Usage: /secrets set NAME=value\n\n{USAGE}"

    try:
        key = store.set(name, value)
    except SecretError as exc:
        return str(exc)
    except OSError as exc:
        return f"Could not write the secrets file: {exc}"

    return (
        f"Stored {key} ({len(value)} chars).\n"
        "Delete the message above to keep the value out of this chat's history."
    )


def _delete(store: SecretStore, rest: str) -> str:
    name = rest.split()[0] if rest.split() else ""
    if not name:
        return f"Usage: /secrets del NAME\n\n{USAGE}"
    try:
        removed = store.delete(name)
    except SecretError as exc:
        return str(exc)
    except OSError as exc:
        return f"Could not write the secrets file: {exc}"
    if removed is None:
        return f"No secret named {name.strip().upper()}."
    return f"Removed {removed}."
