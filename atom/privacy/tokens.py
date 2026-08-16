"""Reversible placeholders for personal data in message text.

An email address becomes ``«email:a91f2c8d»`` on the way into the session, so
history on disk and every provider request carry the placeholder rather than the
address. The placeholder is resolved back on the way out to the user, who owns
the data and sent it in the first place.

This is *pseudonymization*, not anonymization: the map re-identifies every
entity, so the data stays in scope under GDPR. See ``.agent/privacy.md``.

Tool arguments are deliberately not resolved. A token reaching ``web_fetch`` or
``write_file`` stays a token, which is what keeps this from becoming an
exfiltration path — the boundary is "who reads it", not "where it goes".
"""

from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, TypedDict, cast

from loguru import logger

from atom.config.paths import get_private_dir
from atom.privacy.store import write_private_text

TOKENS_FILENAME = "tokens.json"


class TokenEntry(TypedDict):
    """One persisted mapping. ``type`` is stored so new kinds are additive.

    ``created``/``last_used`` are UTC ISO-8601 and ``hits`` counts resolutions.
    They exist to make the map prunable: at :data:`MAX_ENTRIES` an operator has
    to decide what to drop, and without a last-used stamp the only options are
    "delete the file" — which strands every token in saved history — or keep it
    forever. They also answer the question the cap raises on its own: whether a
    full map is full of live entities or of addresses the agent skimmed once.

    Deliberately *not* used for automatic eviction. Dropping an entry makes every
    placeholder for it unresolvable wherever it was already written, so which
    entries die is an operator's call, not a heuristic's.
    """

    type: str
    value: str
    created: str
    last_used: str
    hits: int

#: Bumped only for a breaking change to the file's shape. Entry *types* are
#: additive and need no bump.
#:
#: Adding the usage fields did not bump it: a v1 file written before they existed
#: stays readable, since :func:`_parse_entries` backfills them from the values it
#: has. A reader that ignores them is also still correct — they are metadata about
#: a mapping, not part of it.
SCHEMA_VERSION = 1

#: Written when a pre-usage-fields entry is read back and its real creation time
#: is unknowable. A sentinel rather than "now", because backfilling the load time
#: would make every old entry look freshly minted and quietly destroy the signal
#: the field exists to carry.
UNKNOWN_TIMESTAMP = "unknown"

#: Usage counters are flushed at most this often. Detokenization runs per stream
#: delta, so writing on every resolution would turn one reply into hundreds of
#: fsyncs on the file holding the plaintext map.
_FLUSH_INTERVAL_SECONDS = 30.0

#: A whole address, replaced as one unit — everywhere, including tool output.
#:
#: Splitting an address into its parts (``«user:…»@«domain:…»``) was considered
#: and deferred: it would hide the domain too and shrink the map, but the naming
#: was unsettled, and an entry type is written to disk — renaming one later means
#: migrating live data. Revisit from a clean slate rather than half-introducing
#: it. The cost of not having it: an agent cannot filter tool output by domain,
#: since the domain is inside the placeholder.
TYPE_EMAIL = "email"

#: Shown to the model so it can recognize a placeholder. A literal example
#: rather than a regex: models follow examples more reliably than patterns.
TOKEN_PATTERN_HINT = "«email:a91f2c8d»"

#: Upper bound on stored entries. Tool output is the reason this exists: a single
#: ``grep -r "@"`` over a mail directory can carry thousands of addresses, and the
#: map is append-only, so an unbounded map grows from data the agent merely
#: passed over rather than data anyone chose to keep.
MAX_ENTRIES = 10_000

#: Substituted once :data:`MAX_ENTRIES` is reached. Deliberately not resolvable:
#: leaving the plaintext in place instead would mean the map's size limit
#: silently turns into a disclosure, which is the one outcome this feature exists
#: to prevent. Data is lost rather than leaked, and the loss is logged.
CAPPED_PLACEHOLDER = "«email:capped»"

#: Characters legal in a local part. Named so the pattern and its guard cannot
#: drift apart: the guard's job is to be exactly this set.
_LOCAL_PART_CHARS = r"A-Za-z0-9!#$%&'*+/=?^_`{|}~.-"

#: Deliberately loose on the local part and strict on the shape: a value that
#: does not look like an address must not be replaced, since a wrong
#: substitution corrupts text the agent then reasons over.
#:
#: The lookbehind is a performance guard, not part of the address grammar. A
#: match can only begin where the preceding character could not itself have been
#: part of the local part, which is true of every real address — one is preceded
#: by a space, a quote, a bracket or the start of the text. Without it, a run of
#: *n* local-part characters not followed by a valid domain is retried from all
#: *n* offsets, each failing only after scanning ahead to the ``@``: quadratic in
#: the length of the run. Tool output makes that reachable, since base64 blobs
#: and hex dumps are made of local-part-legal characters, and a single ``@``
#: anywhere past one is enough to defeat the cheap ``"@" not in text`` exit. A
#: 24KB blob cost ~340ms before this and ~0.2ms after, with no change to what
#: matches (verified against the unguarded pattern over 400k fuzzed strings).
_EMAIL_RE = re.compile(
    rf"(?<![{_LOCAL_PART_CHARS}])[{_LOCAL_PART_CHARS}]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)

#: Guillemets cannot appear in an email address and are vanishingly rare in
#: ordinary text, so a placeholder is unambiguous in both directions. This is
#: why ``email+hash`` was rejected: ``+`` is legal in a local part.
_TOKEN_RE = re.compile(r"«([a-z]+):([0-9a-f]{8})»")

_ID_BYTES = 4


def placeholder(entity_type: str, token_id: str) -> str:
    return f"«{entity_type}:{token_id}»"


def _parse_entries(raw: object) -> dict[str, TokenEntry]:
    """Validate the persisted shape once, at the boundary.

    Malformed rows are dropped rather than raising: a hand edit that breaks one
    entry should not strand every other token in saved history.
    """
    entries: dict[str, TokenEntry] = {}
    if not isinstance(raw, dict):
        return entries
    stored = cast(dict[str, Any], raw).get("entries")
    if not isinstance(stored, dict):
        return entries
    for token, entry in cast(dict[Any, Any], stored).items():
        if not isinstance(token, str) or not isinstance(entry, dict):
            continue
        fields = cast(dict[str, Any], entry)
        value = fields.get("value")
        entity_type = fields.get("type")
        if not isinstance(value, str) or not isinstance(entity_type, str):
            continue
        # type and value are load-bearing; the usage fields are not, so a file
        # written before they existed — or hand-edited to drop one — is read
        # rather than discarded. Missing stamps become UNKNOWN_TIMESTAMP instead
        # of the load time, which would make every old entry look new.
        created = fields.get("created")
        last_used = fields.get("last_used")
        hits = fields.get("hits")
        entries[token] = {
            "type": entity_type,
            "value": value,
            "created": created if isinstance(created, str) else UNKNOWN_TIMESTAMP,
            "last_used": last_used if isinstance(last_used, str) else UNKNOWN_TIMESTAMP,
            # bool is an int subclass, so it would otherwise pass as a count.
            "hits": hits if isinstance(hits, int) and not isinstance(hits, bool) else 0,
        }
    return entries


def _utc_now() -> str:
    """UTC ISO-8601 with a ``Z`` suffix, second resolution.

    Second resolution because this is for an operator reading the file, and
    sub-second noise on every entry makes it harder to scan for nothing.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_email(value: str) -> str:
    """Fold case so one address maps to one token.

    The domain is case-insensitive per spec. The local part technically is not,
    but every provider in practice treats it that way, and two tokens for what a
    reader sees as one person is the worse error.

    Gmail dots and ``+tag`` suffixes are deliberately *not* stripped: merging
    addresses a downstream system may treat as distinct cannot be undone.
    """
    local, _, domain = value.partition("@")
    return f"{local.lower()}@{domain.lower()}" if domain else value.lower()


class TokenStore:
    """The token↔value map, persisted to ``private/tokens.json``.

    Tokens are random rather than derived. HMAC would let a token be recomputed
    from a value without the file, which buys nothing here — the map is the
    lookup either way — while adding a key to store, protect and rotate. A
    rotation would also silently orphan every token in every saved session.

    Keyed by token because detokenization is the direction correctness depends
    on: a wrong lookup shows one person's data in place of another's. The
    value→token index is built in memory and never persisted.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path
        self._lock = RLock()
        self._entries: dict[str, TokenEntry] | None = None
        self._by_value: dict[tuple[str, str], str] = {}
        #: Set when the file exists but could not be parsed. Minting stays off
        #: for the process lifetime so a recoverable file is never overwritten.
        self._broken = False
        #: Logged once rather than per value, or a single bulk tool result would
        #: emit thousands of identical lines.
        self._warned_full = False
        #: Usage counters bumped in memory but not yet on disk, and when they
        #: were last flushed. Minting still writes immediately — losing a token
        #: loses data, whereas losing a hit count loses a statistic.
        self._usage_dirty = False
        self._last_flush = 0.0

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        return get_private_dir() / TOKENS_FILENAME

    @property
    def read_path(self) -> Path:
        """Path for reads, without creating the private directory."""
        if self._explicit_path is not None:
            return self._explicit_path
        return get_private_dir(create=False) / TOKENS_FILENAME

    # -- persistence -----------------------------------------------------

    def _load(self) -> dict[str, TokenEntry]:
        if self._entries is not None:
            return self._entries
        try:
            raw = json.loads(self.read_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = None
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt or unreadable map must not take the agent down, but it
            # must also not be overwritten: the file may be recoverable by hand,
            # and rewriting it would strand every token already in saved history
            # as an unresolvable placeholder. Mark it broken so nothing is
            # minted or saved until an operator intervenes.
            logger.error(
                "Could not read {}: {}. Tokenization disabled for this session.",
                self.read_path,
                exc,
            )
            self._broken = True
            self._entries = {}
            return self._entries
        self._entries = entries = _parse_entries(raw)
        self._by_value = {
            (entry["type"], entry["value"]): token
            for token, entry in entries.items()
        }
        return entries

    def _save(self) -> None:
        entries = self._entries or {}
        payload = {"version": SCHEMA_VERSION, "entries": entries}
        write_private_text(
            self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    # -- minting ---------------------------------------------------------

    def token_for(self, entity_type: str, value: str) -> str | None:
        """Return the stable placeholder for *value*, minting one if needed.

        Returns None when the map is unreadable, so the caller leaves the value
        in place rather than minting against state that cannot be saved. Returns
        :data:`CAPPED_PLACEHOLDER` once the map is full — an unresolvable marker,
        because leaving the plaintext would turn a size limit into a disclosure.
        """
        with self._lock:
            entries = self._load()
            if self._broken:
                return None
            key = (entity_type, value)
            existing = self._by_value.get(key)
            if existing is not None:
                return existing

            if len(entries) >= MAX_ENTRIES:
                if not self._warned_full:
                    self._warned_full = True
                    logger.error(
                        "Token map at {} holds {} entries; new values are being "
                        "replaced with {} instead of being stored. Prune the file "
                        "to resume tokenizing new values.",
                        self.path,
                        MAX_ENTRIES,
                        CAPPED_PLACEHOLDER,
                    )
                return CAPPED_PLACEHOLDER

            token = self._mint(entity_type, entries)
            now = _utc_now()
            entries[token] = {
                "type": entity_type,
                "value": value,
                "created": now,
                # Seeded to `created` rather than left blank so the field always
                # answers "when was this last relevant" without a null case; a
                # minted-but-never-resolved entry reads as hits: 0.
                "last_used": now,
                "hits": 0,
            }
            self._by_value[key] = token
            self._save()
            return token

    def _mint(self, entity_type: str, entries: dict[str, TokenEntry]) -> str:
        """Draw an unused placeholder.

        Random, so collisions are possible and simply redrawn — the reason the
        deterministic-token design needed a stored disambiguator and this does
        not.
        """
        for _ in range(1000):
            candidate = placeholder(entity_type, secrets.token_hex(_ID_BYTES))
            if candidate not in entries:
                return candidate
        raise RuntimeError("Could not mint an unused token")

    def value_for(self, token: str) -> str | None:
        """Return the value behind *token*, or None when it is not in the map.

        Records the resolution. The write is deferred (see :meth:`_touch`), so a
        lookup stays a lookup as far as latency is concerned.
        """
        with self._lock:
            entry = self._load().get(token)
            if entry is None:
                return None
            self._touch(entry)
            return entry["value"]

    def _touch(self, entry: TokenEntry) -> None:
        """Record one resolution, flushing at most once per interval.

        Caller holds the lock. Counters are advanced in memory immediately and
        persisted on a timer: detokenization runs once per stream delta, so
        writing through would mean hundreds of rewrites of the plaintext map for
        a single reply. A crash costs at most one interval of counters, which is
        the right thing to lose — the mappings themselves are already durable
        because minting writes synchronously.
        """
        # Read defensively rather than by subscript: this runs on the egress path
        # that resolves placeholders for the user, and an entry reaching here
        # without the usage keys must not turn a reply into a KeyError. The
        # parser backfills anything loaded from disk, so this covers entries
        # constructed in process.
        # A non-int `hits` cannot reach here: the parser coerces it on load and
        # the type says int, so `.get`'s default covers the only real case —
        # a key that was never set.
        entry["hits"] = entry.get("hits", 0) + 1
        entry["last_used"] = _utc_now()
        entry.setdefault("created", UNKNOWN_TIMESTAMP)
        self._usage_dirty = True
        now = time.monotonic()
        # `_last_flush` starts at 0.0, so the first resolution writes through and
        # only the burst behind it is throttled. That is the useful shape: a
        # process that resolves a token once and exits still records it without
        # depending on anything calling `flush`, and one write is cheap. The
        # window then absorbs a stream's worth of deltas.
        if now - self._last_flush < _FLUSH_INTERVAL_SECONDS:
            return
        self._last_flush = now
        self._flush_usage()

    def _flush_usage(self) -> None:
        """Persist pending counters. Caller holds the lock.

        Failure is logged and swallowed: usage metadata is not worth failing a
        turn over, and the counters stay in memory to go out with the next flush.
        """
        if not self._usage_dirty or self._broken:
            return
        try:
            self._save()
        except OSError as exc:
            logger.warning("Could not persist token usage counters: {}", exc)
            return
        self._usage_dirty = False

    def flush(self) -> None:
        """Persist pending usage counters now.

        Public because the deferred write has one bad ending: a process that
        exits between flushes drops counters that were only ever in memory.
        """
        with self._lock:
            self._flush_usage()

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())

    def __bool__(self) -> bool:
        """Always true, so a store is never mistaken for "no store".

        Without this, ``__len__`` makes an empty store falsy and the idiomatic
        ``store or DEFAULT_TOKEN_STORE`` silently swaps a caller's store for the
        default one during precisely the window it is empty. That sent values
        into the real map and is the kind of bug a reader of the call site cannot
        see, so the trap is closed here rather than only at the two call sites.
        """
        return True


DEFAULT_TOKEN_STORE = TokenStore()


def tokenize(text: str, store: TokenStore | None = None) -> str:
    """Replace email addresses in *text* with stable placeholders.

    Pure text in, pure text out, with no knowledge of channels or messages, so
    the same call serves message text, tool output, subagent results and
    transcription alike.
    """
    if not text or "@" not in text:
        return text
    # `store or DEFAULT_TOKEN_STORE` was wrong: __len__ makes an empty store
    # falsy, so an explicitly passed store was discarded for exactly as long as
    # it was empty — its whole first-use window. Values then went to the default
    # map instead, which is the user's real one.
    resolved = store if store is not None else DEFAULT_TOKEN_STORE

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            return resolved.token_for(TYPE_EMAIL, canonical_email(raw)) or raw
        except (OSError, RuntimeError) as exc:
            # Leaving the value in place is the pre-existing behaviour; failing
            # the turn because a map could not be written would be worse.
            logger.warning("Tokenization failed, leaving value in place: {}", exc)
            return raw

    return _EMAIL_RE.sub(_replace, text)


def detokenize(text: str, store: TokenStore | None = None) -> str:
    """Resolve placeholders in *text* back to their values.

    Unknown placeholders are left as-is: they may predate a lost map, and
    inventing a value would be worse than showing the marker.
    """
    if not text or "«" not in text:
        return text
    # `is not None` rather than `or`: see the note in tokenize(). An empty store
    # is falsy, and silently resolving against the default map would show one
    # caller's values to another.
    resolved = store if store is not None else DEFAULT_TOKEN_STORE

    def _replace(match: re.Match[str]) -> str:
        return resolved.value_for(match.group(0)) or match.group(0)

    return _TOKEN_RE.sub(_replace, text)
