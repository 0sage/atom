"""``/mask`` command handling.

Declares a literal value as sensitive, so it is replaced with a typed placeholder
everywhere tokenization runs. Email addresses are *discovered* by a pattern; a name
has no reliable pattern, so this command is the detection.

Dispatched on the router's priority tier, like ``/secrets``: the command text
contains the value in plaintext, and the priority tier is the path that writes
nothing to session history and sends nothing to the model.

Replies never quote the value. The reply travels back through the same chat the
value arrived on, so echoing it would undo the point of the command — and the
user's own message still needs deleting, which is what ``carried_value`` asks for.
"""

from __future__ import annotations

from dataclasses import dataclass

from atom.privacy import tokens as tokens_module
from atom.privacy.tokens import MASK_TYPES, MaskError, TokenStore, validate_mask

USAGE = (
    "Usage:\n"
    "/mask — list what is masked\n"
    "/mask TYPE value — mask a value\n"
    "/mask del value — stop masking a value\n\n"
    "Documented types: "
    + ", ".join(sorted(MASK_TYPES))
    + "\n\nAny other single lowercase word works too, so /mask iban <value> needs "
    "no upgrade. A type must be one lowercase word — no digits, spaces, hyphens "
    "or underscores.\n\n"
    "Example: /mask name Alexey\n"
    "The type is required: nothing in a value says whether it is a first name or "
    "a company, and the model uses the type to choose correct grammar."
)


@dataclass(frozen=True)
class MaskReply:
    """Reply text plus whether the command carried a sensitive value.

    ``carried_value`` asks the channel to delete the user's message. Set whenever
    the input *contained* a value — including when the command was rejected,
    because a value typed alongside a bad type was still typed.
    """

    text: str
    carried_value: bool = False


def handle_mask_command(args: str, store: TokenStore | None = None) -> MaskReply:
    """Handle ``/mask`` and return the reply.

    *args* is passed verbatim, without case folding: a value's capitalization is
    what gets shown back when the placeholder is resolved.
    """
    # Resolved through the module rather than bound at import, so the default
    # store stays swappable — a test that silently writes the real
    # ~/.atom/private/tokens.json is the failure mode this must not have.
    store = store or tokens_module.DEFAULT_TOKEN_STORE
    text = args.strip()

    if not text or text.lower() == "list":
        return MaskReply(_list(store))

    verb, _, rest = text.partition(" ")
    rest = rest.strip()

    if verb.lower() in {"help", "-h", "--help"}:
        return MaskReply(USAGE)

    if verb.lower() in {"del", "delete", "rm", "remove", "unmask"}:
        return _delete(store, rest)

    return _add(store, verb, rest)


def _list(store: TokenStore) -> str:
    """Inventory: enough to identify what is masked, without printing the values.

    The type and length identify an entry for someone deciding what to remove.
    Printing the values would put every masked name back into the chat at once,
    which is the opposite of what the feature is for.
    """
    try:
        masks = store.masks()
    except OSError as exc:
        return f"Could not read the token map: {exc}"
    if not masks:
        return f"Nothing is masked.\n\n{USAGE}"

    lines = [f"Masked values ({len(masks)}):"]
    for _token, entity_type, value in sorted(masks, key=lambda item: (item[1], item[2])):
        lines.append(f"- {entity_type} ({len(value)} chars)")
    lines.append("")
    lines.append("Values are not shown here. Use /mask del <value> to stop masking one.")
    return "\n".join(lines)


def _add(store: TokenStore, entity_type: str, value: str) -> MaskReply:
    """Declare a value. Every exit past validation deletes the user's message."""
    if not value:
        # No value typed, so nothing to delete — but a bare type is the shape of
        # someone about to type one, so show the usage rather than an error.
        return MaskReply(f"Usage: /mask {entity_type.lower()} <value>\n\n{USAGE}")

    try:
        checked_type, checked_value = validate_mask(entity_type, value)
    except MaskError as exc:
        return MaskReply(str(exc), carried_value=True)

    try:
        token = store.add_mask(checked_type, checked_value)
    except OSError as exc:
        return MaskReply(f"Could not write the token map: {exc}", carried_value=True)

    if token is None:
        # `add_mask` returns None only when the map is unreadable or full, and
        # both are conditions an operator has to act on.
        return MaskReply(
            "Could not store the mask: the token map is unreadable or full. "
            "Check the gateway log.",
            carried_value=True,
        )

    lines = [
        f"Masking as {checked_type} ({len(checked_value)} chars). "
        "It will be replaced in messages and tool output from now on; "
        "text already in history is unchanged."
    ]

    if checked_type not in MASK_TYPES:
        # The cost of an open type namespace: `/mask nmae Alexey` used to be
        # refused with the valid types listed, and now succeeds. Naming the new
        # type back is what makes a typo visible in the one place the user is
        # already looking — the value is never quoted, only the type.
        lines.append(
            f"\nNote: '{checked_type}' is a new type, so the model gets no "
            "description of what it means. If that was a typo, /mask del <value> "
            "undoes it."
        )

    return MaskReply("\n".join(lines), carried_value=True)


def _delete(store: TokenStore, value: str) -> MaskReply:
    if not value:
        return MaskReply(f"Usage: /mask del <value>\n\n{USAGE}")
    try:
        token = store.remove_mask(value)
    except OSError as exc:
        return MaskReply(f"Could not write the token map: {exc}", carried_value=True)

    if token is None:
        return MaskReply("That value is not masked.", carried_value=True)
    return MaskReply(
        "No longer masking it. Placeholders already written into saved history "
        "will not resolve any more — the stored value is gone.",
        carried_value=True,
    )
