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
from collections.abc import Generator
from contextlib import contextmanager
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

#: Types for values an operator *declares* with ``/mask``, rather than ones a
#: pattern discovers. There is no reliable regex for a person's name, so the
#: command is the detection; these say what the declared value *is*.
#:
#: Typed rather than one generic ``text`` because the type is the only thing the
#: model sees. ``«text:a1b2c3d4» sent an invoice to «text:99887766»`` leaves it
#: unable to tell a person from a street from a company, so it cannot choose
#: "they" over "it" or write grammatical prose around the placeholder — and
#: ``_TOKEN_GUIDANCE`` exists precisely because it behaves badly when it cannot
#: tell. Resolution is type-agnostic, so the label costs nothing structurally.
#:
#: A closed set, not a free-form field: a typo (``/mask nmae``) would otherwise
#: mint a type the model has no guidance for. Adding one is a one-line change and
#: needs no schema bump — but *renaming* one means migrating live data, since the
#: type is written to disk inside every placeholder.
TYPE_NAME = "name"
TYPE_SURNAME = "surname"
TYPE_ADDRESS = "address"
TYPE_PHONE = "phone"
TYPE_COMPANY = "company"

#: The escape hatch, for a value that genuinely fits none of the above — an
#: internal codename, say. Deliberately *alongside* the specific types rather
#: than instead of them: as a default it would quietly become the common case and
#: give the model back the ambiguity the specific types remove.
TYPE_TEXT = "text"

#: What ``/mask`` accepts, mapped to a short description for its usage text and
#: for the guidance the model receives.
MASK_TYPES: dict[str, str] = {
    TYPE_NAME: "a person's given name",
    TYPE_SURNAME: "a person's family name",
    TYPE_ADDRESS: "a postal address or part of one",
    TYPE_PHONE: "a telephone number",
    TYPE_COMPANY: "an organization's name",
    TYPE_TEXT: "a sensitive string that fits no other type",
}

#: Shortest value ``/mask`` will register. Two- and three-letter values are the
#: one real hazard here: masking ``An`` rewrites "an update", and masking ``Read``
#: rewrites "please read", so the agent reasons over corrupted text — the exact
#: failure the email pattern's strictness exists to avoid. Measured, not guessed:
#: both match ordinary prose twice in a single test sentence.
MIN_MASK_LENGTH = 4

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


#: Last formatted stamp, as ``(unix_second, text)``. Held at module level rather
#: than per store so two stores in one process share it; the value depends only on
#: the clock. Lowercase because it is reassigned — an uppercase name reads as a
#: constant and basedpyright rejects rebinding one.
_stamp_cache: tuple[int, str] = (-1, "")


def _utc_now() -> str:
    """UTC ISO-8601 with a ``Z`` suffix, second resolution.

    Second resolution because this is for an operator reading the file, and
    sub-second noise on every entry makes it harder to scan for nothing.

    Cached for the second it describes. ``strftime`` measured 1.36us against
    0.03us for the rest of :meth:`TokenStore._touch` combined — 94% of the work
    done on every resolution, spent formatting a string that cannot have changed.
    Egress calls this once per placeholder, so a reply carrying 200 of them paid
    for 200 identical stamps.

    No lock: the worst a race can do is have two threads format the same second
    and assign the same tuple. A stale read costs one extra format.
    """
    global _stamp_cache
    now = time.time()
    second = int(now)
    cached_second, cached_text = _stamp_cache
    if second == cached_second:
        return cached_text
    text = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _stamp_cache = (second, text)
    return text


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


class MaskError(ValueError):
    """A ``/mask`` request that must be refused rather than stored.

    Carries a message safe to show the user: it names the *rule* that was broken
    and never quotes the value, since the reply travels back through the same chat
    channel the value arrived on.
    """


def validate_mask(entity_type: str, value: str) -> tuple[str, str]:
    """Check a ``/mask`` request and return the normalized ``(type, value)``.

    Raises :exc:`MaskError` rather than returning a flag, so a caller cannot
    forget to check. Every rule here exists because breaking it corrupts the text
    the agent reasons over, which is worse than not masking at all.
    """
    entity_type = entity_type.strip().casefold()
    if entity_type not in MASK_TYPES:
        known = ", ".join(sorted(MASK_TYPES))
        raise MaskError(f"Unknown type. Use one of: {known}")

    # Collapse internal runs of whitespace so "Acme   Corp" and "Acme Corp" are
    # one mask. A literal with a doubled space would otherwise never match text
    # that a human retyped normally.
    value = " ".join(value.split())
    if not value:
        raise MaskError("Nothing to mask.")

    if len(value) < MIN_MASK_LENGTH:
        raise MaskError(
            f"Too short to mask safely (minimum {MIN_MASK_LENGTH} characters). "
            "A short value matches ordinary words — masking 'An' would rewrite "
            "'an update' — and the agent then reads corrupted text."
        )

    # A value made only of punctuation or digits has no word boundary to anchor
    # against, so `\b(?:…)\b` would match in places a reader would not expect.
    if not any(char.isalnum() for char in value):
        raise MaskError("A mask needs at least one letter or digit.")

    if "«" in value or "»" in value:
        raise MaskError(
            "Guillemets are reserved for placeholders and cannot appear in a mask."
        )

    return entity_type, value


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
        #: Declared masks keyed by casefolded value. Separate from ``_by_value``,
        #: which is keyed by ``(type, exact value)``: a mask is matched
        #: case-insensitively and without knowing its type, since the alternation
        #: that found it in the text carries neither. Without this index every hit
        #: scanned the whole map and casefolded every entry — O(masks x hits),
        #: measured at 2.1M hits/s for one mask against 113k for a thousand.
        self._by_folded_mask: dict[str, str] = {}
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
        #: Depth of nested :meth:`minting_batch` blocks, and whether a mint has
        #: happened inside the outermost one. Nesting is counted rather than
        #: flagged so an inner block cannot save early and leave the outer one
        #: thinking its work is durable. Zero means every mint saves immediately,
        #: which keeps a direct ``token_for`` call as durable as it has always
        #: been.
        self._batch_depth = 0
        self._mint_dirty = False
        #: Compiled alternation over declared masks, plus the mask set it was
        #: built from. Cached because recompiling a 1000-branch regex per message
        #: would cost more than the match; invalidated by comparing the signature
        #: rather than by every writer remembering to clear it. ``None`` signature
        #: forces a rebuild.
        self._mask_pattern: re.Pattern[str] | None = None
        self._mask_signature: tuple[str, ...] | None = None

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
        # Built once per load rather than scanned per hit. Later entries win a
        # collision, matching `_by_value`'s behaviour: two entries whose values
        # differ only by case should not exist, and if a hand-edited file contains
        # them, resolving consistently to one beats alternating between them.
        self._by_folded_mask = {
            entry["value"].casefold(): token
            for token, entry in entries.items()
            if entry["type"] != TYPE_EMAIL
        }
        return entries

    def _save(self) -> None:
        entries = self._entries or {}
        payload = {"version": SCHEMA_VERSION, "entries": entries}
        write_private_text(
            self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    @contextmanager
    def minting_batch(self) -> Generator[None]:
        """Hold back per-value saves, writing once when the block exits.

        ``_save`` rewrites the whole map, so saving per newly minted value makes
        minting *n* values quadratic in bytes written: 800 addresses wrote 65MB to
        produce a 160KB file, and reaching :data:`MAX_ENTRIES` took ~130s on fast
        local storage and ~400s on flash. One save per block makes it linear —
        measured 131x faster at 2,000 values on ARM/flash, and it converts a bulk
        tool result from minutes into ~0.4s.

        Only *minting* is affected. A value already in the map is answered from
        the in-memory index and never wrote anything, batched or not.

        The lock is held for the whole block, so a concurrent minter waits rather
        than interleaving. Minting already serialized on this lock per value; what
        changes is the granularity, and the alternative — letting one thread's
        exit decide whether another thread's entries are durable — is the kind of
        ordering bug that only shows up under load.

        The save is inside the block, so it happens before the caller can act on
        the returned text. An :exc:`OSError` propagates: by the time it fires the
        substitutions have already been made, so swallowing it would hand back
        placeholders with no entry to resolve them. Failing the call is the
        deliberate choice — this feature exists to prevent disclosure, and a
        broken write must not become one.
        """
        with self._lock:
            self._batch_depth += 1
            try:
                yield
            finally:
                self._batch_depth -= 1
            if self._batch_depth == 0 and self._mint_dirty:
                self._save()
                self._mint_dirty = False
                # The save wrote the whole map, so any pending counters went with
                # it. `_last_flush` is deliberately *not* touched: it starts at
                # 0.0 so the first resolution writes through, and restarting the
                # timer here would put that first resolution inside the throttle
                # window instead.
                self._usage_dirty = False

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
            if entity_type != TYPE_EMAIL:
                self._by_folded_mask[value.casefold()] = token
            if self._batch_depth:
                # Inside a batch the save is deferred to the block's exit. The
                # entry is already in `_entries` and `_by_value`, so it resolves
                # in this process either way; what is deferred is durability.
                self._mint_dirty = True
            else:
                self._save()
            return token

    # -- declared masks --------------------------------------------------

    def masks(self) -> list[tuple[str, str, str]]:
        """Every declared mask as ``(token, type, value)``, longest value first.

        Longest-first is not cosmetic. With both ``Sage`` and ``Sage Smith``
        registered, matching in insertion order turns "Sage Smith called" into
        ``«name:…» Smith called`` — the surname survives in plaintext. Ordering by
        descending length makes the alternation prefer the longer value.

        Email entries are excluded: they are discovered by a pattern, not declared,
        and re-matching them as literals would be redundant work on every call.
        """
        with self._lock:
            declared = [
                (token, entry["type"], entry["value"])
                for token, entry in self._load().items()
                if entry["type"] != TYPE_EMAIL
            ]
        return sorted(declared, key=lambda item: (-len(item[2]), item[2]))

    def mask_pattern(self) -> re.Pattern[str] | None:
        """One alternation over every declared value, or None when there are none.

        Rebuilt on demand and cached against the map's identity, because a
        ``/mask`` mid-session must take effect on the next message rather than the
        next restart.

        Cost is flat in the number of masks: measured 0.21ms over 35KB of clean
        text for 1 literal and 0.21ms for 1000, because the engine prefilters the
        alternation. Case-insensitive costs the same again.

        ``\\b`` guards both ends so a mask cannot rewrite a longer word — without
        it, masking ``Sage`` turns ``Sagebrush`` into ``«name:…»brush`` and the
        agent then reasons over corrupted text.
        """
        with self._lock:
            entries = self._load()
            # Identity of the current mask set: rebuilding on every call would
            # recompile a 1000-branch regex per message, and comparing the tuple
            # is cheaper than the compile by orders of magnitude.
            signature = tuple(
                token for token, entry in entries.items() if entry["type"] != TYPE_EMAIL
            )
            if self._mask_signature == signature:
                return self._mask_pattern
            declared = self.masks()
            if not declared:
                self._mask_signature = signature
                self._mask_pattern = None
                return None
            alternation = "|".join(re.escape(value) for _, _, value in declared)
            self._mask_pattern = re.compile(
                rf"\b(?:{alternation})\b", re.IGNORECASE
            )
            self._mask_signature = signature
            return self._mask_pattern

    def token_for_mask(self, value: str) -> str | None:
        """Return the placeholder for a declared *value*, matched case-insensitively.

        Case folding mirrors :func:`canonical_email`: one person should not get two
        tokens because a sender capitalized differently. The stored value is what
        detokenization shows back, so "alexey" registered and "Alexey" written
        resolves to the registered spelling — deliberately lossy on display, the
        same trade the email path already makes.

        One dict lookup, not a scan. This runs once per matched occurrence, so
        scanning the map and casefolding every entry per hit made ingress
        degrade with registry size — 2.1M hits/s for one mask against 113k for
        a thousand, measured. The index makes it flat.
        """
        folded = value.casefold()
        with self._lock:
            self._load()
            return self._by_folded_mask.get(folded)

    def add_mask(self, entity_type: str, value: str) -> str | None:
        """Declare *value* as sensitive and return its placeholder.

        Returns the existing token when the value is already declared, so running
        ``/mask`` twice is idempotent rather than minting a second token for one
        person. Returns None when the map is unreadable or full, matching
        :meth:`token_for`.
        """
        existing = self.token_for_mask(value)
        if existing is not None:
            return existing
        return self.token_for(entity_type, value)

    def remove_mask(self, value: str) -> str | None:
        """Undeclare *value*, returning the token that is now unresolvable.

        The entry is deleted rather than flagged, which means every placeholder
        for it already written into saved history stops resolving. That is the
        honest outcome — the alternative is keeping the plaintext on disk after an
        operator asked for it to be forgotten — and it is why the reply says so.
        """
        token = self.token_for_mask(value)
        if token is None:
            return None
        with self._lock:
            entries = self._load()
            entry = entries.pop(token, None)
            if entry is not None:
                self._by_value.pop((entry["type"], entry["value"]), None)
                self._by_folded_mask.pop(entry["value"].casefold(), None)
            self._mask_signature = None
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
    """Replace email addresses and declared masks in *text* with placeholders.

    Pure text in, pure text out, with no knowledge of channels or messages, so
    the same call serves message text, tool output, subagent results and
    transcription alike.

    Two kinds of substitution, deliberately kept separate. An address is
    *discovered* by :data:`_EMAIL_RE`, so it is replaced whether or not anyone
    registered it. A mask is *declared* with ``/mask``, because there is no
    reliable pattern for a person's name — the command is the detection.

    Masks are matched after addresses so a masked value inside an address cannot
    break the address apart: by then the address is already a placeholder, and
    ``\\b`` cannot match inside one.
    """
    if not text:
        return text
    # `store or DEFAULT_TOKEN_STORE` was wrong: __len__ makes an empty store
    # falsy, so an explicitly passed store was discarded for exactly as long as
    # it was empty — its whole first-use window. Values then went to the default
    # map instead, which is the user's real one.
    resolved = store if store is not None else DEFAULT_TOKEN_STORE

    # The two cheap gates, checked before any scanning. `"@" not in text` is what
    # keeps address-free text free, and `mask_pattern()` returns None when nothing
    # is declared, so an operator who never runs `/mask` pays one attribute lookup.
    # Skipping the batch entirely when neither applies matters: entering it takes
    # the store's lock, and most calls have nothing to substitute.
    has_address = "@" in text
    mask_pattern = resolved.mask_pattern()
    if not has_address and mask_pattern is None:
        return text

    def _replace_email(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            return resolved.token_for(TYPE_EMAIL, canonical_email(raw)) or raw
        except (OSError, RuntimeError) as exc:
            # One value could not be minted. Leaving it in place is the
            # pre-existing behaviour and stays correct: nothing was substituted
            # for it, so the text remains internally consistent.
            logger.warning("Tokenization failed, leaving value in place: {}", exc)
            return raw

    def _replace_mask(match: re.Match[str]) -> str:
        raw = match.group(0)
        # Already declared, so a token exists; a miss means the pattern and the
        # map disagreed, and leaving the text alone is safer than minting a
        # second entry for a value the operator already registered.
        return resolved.token_for_mask(raw) or raw

    # One save for the whole text instead of one per new address. Inside the
    # batch `token_for` no longer writes, so a tool result carrying thousands of
    # new addresses costs one rewrite of the map rather than thousands of
    # progressively larger ones — linear instead of quadratic.
    #
    # An OSError from the batch's save propagates. It cannot be swallowed here:
    # every substitution has already been made by then, so returning the text
    # would hand back placeholders with no stored entry behind them, and the
    # addresses would be unrecoverable. Failing the call keeps the invariant that
    # a placeholder in returned text is always resolvable.
    with resolved.minting_batch():
        if has_address:
            text = _EMAIL_RE.sub(_replace_email, text)
        if mask_pattern is not None:
            text = mask_pattern.sub(_replace_mask, text)
        return text


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
