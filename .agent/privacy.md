# Privacy: Secrets and Email Tokenization

Design record for `atom/privacy/`. Records the decisions behind the shipped
behaviour and the ones deliberately deferred, so neither gets re-litigated or
silently reversed.

Two independent features live here:

- **Secret store** — operator-declared values the agent uses without seeing.
- **Email tokenization** — personal data replaced with placeholders on the way
  in and resolved on the way out.

## What is implemented: secret store

Operator-declared secrets, referenced by name and never shown to the model:

1. `/secrets` stores a value in `~/.atom/private/secrets.env`.
2. The agent is told the *names* only, via the `exec` tool description and a
   per-turn runtime context block.
3. `ExecTool._build_env` injects the values into the subprocess environment.
4. The agent writes `$NAME`; the shell expands it inside the child process.

The value never enters a prompt, a tool argument, or session history.

## What this is not

Data minimization on the way in. It is **not** containment: the agent has a
shell, and a shell can read its own environment. `env`, `printenv`, `curl -v`,
`set -x`, a stack trace, or `ps aux` will all surface an injected value, and the
result of such a command *is* persisted to history and sent to the provider.

Containment is the exec sandbox and tool policy (`.agent/security.md`); this
feature does not extend them. Do not describe it as preventing leaks.

A redaction pass over command output was designed and deliberately rejected:
the operator's threat model does not care whether a value returns through tool
output, and the pass cost either corrupted unrelated output (short values) or
gave false confidence (encoded values slip through a literal match). Keeping the
logic simple was the explicit call. Revisit only if the threat model changes.

## Layout

```
~/.atom/private/          # 0700
  secrets.env             # 0600, operator-authored, plaintext values
  tokens.json             # 0600, runtime-written, token → {type, value}
```

Both hold plaintext, so both are 0600. `tokens.json` is the *only* place a
tokenized address exists in plaintext — history, provider requests and tool
arguments carry the placeholder.

`~/.atom`, never the workspace: the workspace is exactly the tree the agent can
read via `read_file` and `cat`, so plaintext there would defeat the purpose. It
is also denied by the workspace path resolver, being a parent of the default
`~/.atom/workspace` — a property to preserve, not an accident.

Not in `config.json` either: that file is loaded, dumped, watched
(`config/watcher.py`), and echoed by CLI config commands.

`get_private_dir(create=False)` exists because `load` runs on the hot path that
builds a subprocess environment. A read must not create a directory as a side
effect, nor fail when the parent is unwritable — an early version broke every
shell command when `HOME` pointed somewhere unwritable.

## Name rules

`[A-Z_][A-Z0-9_]*`, at most 64 characters, not reserved.

- **Uppercase only, POSIX-shaped**, because these become environment variables
  expanded as `$NAME`. Anything else either fails to expand or changes the
  meaning of the surrounding command.
- **Input is normalized, not matched loosely.** `/secrets del token` uppercases
  to `TOKEN`. Case-*insensitive matching* was rejected: env vars are
  case-sensitive, so `TOKEN` and `token` are genuinely different variables, and
  a store holding both would give `del` a silent choice. Normalizing on the way
  in means only one case can ever exist, which is stricter and still solves the
  mobile-typing problem. Consequence accepted: lowercase env vars
  (`http_proxy`, `npm_config_*`) are unreachable through this feature.
- **Length cap** so a 10KB name cannot be written as a valid `.env` line.
- **Reserved names rejected** (`RESERVED_NAMES`): those `ExecTool` sets itself,
  plus `PATH`, `LD_PRELOAD`, `PYTHONPATH` and friends. A settable `PATH` would
  let anyone with command access redirect every binary the agent invokes.

Two guards cover reserved names, deliberately: validation fails loudly at
`/secrets set`, and `load` drops them so a hand-edited file cannot smuggle one
in. Injection precedence is the third — base environment keys always win.

## Value rules

Any characters except line breaks and null bytes.

A newline would write a second assignment from one command
(`TOKEN=a\nADMIN_KEY=b`), which is an escalation path if a later feature grants
meaning to a specific name. Rejected rather than escaped: quoting only moves the
problem into a parser.

## File format

Line-oriented, not a dict round-trip, so operator comments and ordering survive
a write from `/secrets`. Values are single-quoted on write; reads also accept
double-quoted, unquoted, and `export `-prefixed forms so a hand edit works.

Writes are atomic (temp, fsync, `os.replace`, fsync the directory) at mode
0600. `utils.helpers._write_text_atomic` could not be reused: it opens the temp
file at the process umask and chmods afterwards, which would leave the secret
briefly world-readable. `store._write_secret_file` opens with 0600 from the
start.

Loose modes on an existing file warn rather than fail — an operator may have
relaxed it deliberately, and refusing to load would break a working setup at
startup.

## Command surface

```
/secrets                    list names and value lengths
/secrets set NAME=value     create or replace
/secrets del NAME           remove
```

- **Plural**, matching `secrets.env` and the package. The singular `/secret` is
  registered as an alias because it is the natural typo.
- **Priority tier** (`router.priority_prefix`). This is the load-bearing
  decision: `_dispatch_command` persists both the command text and the reply to
  session history, while `_dispatch_command_inline` runs with `session=None` and
  persists nothing. A value typed into `/secrets set` must never reach the
  transcript, so it must dispatch pre-lock. `dispatch_priority` slices arguments
  from the original text, not the case-folded copy, because a value's case is
  significant.
- **No `get`.** Printing a value would re-inject it into the chat and whatever
  renders the reply. The listing shows `TOKEN (40 chars)` — enough to verify the
  right thing is stored, not enough to use.
- **Replies never echo a value, including on error.** A mistyped verb still
  carries the value, so the unknown-subcommand reply does not quote its input.

Works on every channel: `AgentLoop` owns the single router, and the CLI
publishes to the same bus, so Telegram, the CLI TUI, and the WebUI all get it.
The CLI is the safer path — the value never leaves the machine.

## Deleting the user's message

`handle_secrets_command` returns `SecretsReply(text, carried_value)`.
`carried_value` sets `OUTBOUND_META_DELETE_SOURCE` on the reply, which asks the
channel to delete the message identified by `metadata["message_id"]`. Telegram
honors it in `_delete_source_message`; channels that cannot delete ignore the
flag.

`carried_value` is set whenever a value was **typed**, not only when it was
stored. A rejected name (`set BAD-NAME=ghp_real`), a write failure, and a
mistyped verb (`sett TOKEN=ghp_real`) all leave a real secret in the chat, so
each one still requests deletion. Conversely `list`, `del NAME`, `help` and the
usage errors carry no value, and deleting those would destroy the user's own
text for nothing.

Best-effort by construction, and the reply must never claim otherwise: Telegram
refuses to delete messages older than 48 hours, and a bot without
`can_delete_messages` cannot delete in a group at all. The confirmation is sent
regardless of whether the delete succeeded — otherwise the user cannot tell
whether the secret was stored. The reply says "Value redacted from this chat"
rather than "message deleted" for the same reason.

This narrows the gap below; it does not close it. The plaintext still reached
Telegram's servers, and a deletion does not reach anyone who already saw it.

## Known gaps

- **The chat channel sees the value.** Typing `/secrets set` in Telegram puts
  the plaintext in the sender's message history and on Telegram's servers before
  atom ever sees it. Priority dispatch protects atom's disk; the automatic
  deletion above removes the message afterwards, but neither unsends what
  already arrived. This risk was explicitly accepted; the CLI avoids it.
- **No authorization.** The router does not restrict who may run a command, so
  in a group chat any member can set a secret that the shell tool then inherits.
  Gating on the pairing store, or restricting to DMs, is unbuilt.
- **Cross-process writes are last-write-wins.** The in-process lock serializes
  atom's own access; the CLI running against a live gateway is not coordinated.
  The write itself is atomic, so the file is never torn.
- **Values are readable by anything running as the user.** 0600 is filesystem
  permissions, not encryption.

## What is implemented: email tokenization

On by default (`privacy.tokenizeEmails`). An address becomes `«email:a91f2c8d»`
wherever it enters the transcript, and is resolved on the way out to the user:

```
in  (user text):    alex@example.com  →  «email:a91f2c8d»
in  (tool output):  alex@example.com  →  «email:a91f2c8d»
    on disk + provider:                  «email:a91f2c8d»
out (to the user):  «email:a91f2c8d»  →  alex@example.com
    tool arguments:                      «email:a91f2c8d»  (unchanged)
```

The rule is **who reads it**, not where it goes. The user owns the data and sent
it, so showing it back is not a disclosure. A placeholder passed to `web_fetch`
or `write_file` stays a placeholder, which is what keeps this from becoming an
exfiltration path — and is why no per-tool detokenization allowlist is needed.
That allowlist was the riskiest part of the original design; scoping egress to
"text going to the user" removed the need for it entirely.

This is *pseudonymization*, not anonymization: the map re-identifies every
entity, so the data stays in scope under GDPR. Do not name a config key,
command, or docstring `anonymize`.

### Why ingress rather than egress-to-provider

There is no single pre-provider boundary: `chat`/`chat_stream` exist separately
on `anthropic_provider`, `openai_compat_provider`, `fallback_provider` and
`base`, plus `transcription` and `image_generation`. Tokenizing there means
getting every one right, and the plaintext would still be in `history.jsonl`.
Tokenizing on ingress covers history and every provider in one place.

Slash commands are skipped: their arguments go to a command handler rather than
a model, and `/secrets set` must reach the store byte-for-byte.

### Four ingress paths, because none of them shares a chokepoint

An earlier version tokenized only message text, which protected the data the user
already controls and left the bulk exposure open. `exec` running a query,
`web_fetch` reading an endpoint, or an MCP mail server listing an inbox each
carry hundreds of third-party addresses straight into history and the provider.

| Path | Hook |
| --- | --- |
| user message text | `_process_message` → `tokenize_user_text` |
| tool output | `normalize_tool_result` → `tokenize_tool_result` |
| subagent results | `_persist_subagent_followup` → `tokenize_injected_text` |
| voice transcription | covered by the message hook |

`normalize_tool_result` (`context_governance.py:111`) covers `exec`,
`web_fetch` and every MCP tool at once, because `MCPToolWrapper` is an ordinary
`Tool` whose result flows through the same path. The hook runs *before* the
`TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS` early return and before
`maybe_persist_tool_result`, so both the model copy and the offloaded file carry
placeholders.

Transcription needs no hook of its own: Telegram folds it into
`InboundMessage.content` (`runtime.py:1308`) before `_process_message` runs.
Verified, not assumed.

Subagent results *should* already be tokenized — a subagent's prompt and tool
results both pass through hooks — but they arrive on the `system` channel, which
the user-text hook skips. The hook there is a backstop, and a no-op when the
reasoning holds, since `tokenize` only matches addresses and a placeholder is not
one.

### Files are read through tools, so they are covered now

The earlier "files are deliberately untouched" decision is superseded for
practical purposes: `read_file` is a tool, so its output is tokenized like any
other. The original objection — that the agent could read the original anyway —
turned out to be answering the wrong question. What matters is not whether the
agent *can* reach a value, but whether the value **lands in the transcript and
goes to the provider**. Tool-result tokenization stops that, for files too.

### Tokens are random, not derived

HMAC would let a token be recomputed from a value without the file, which buys
nothing here — the map is the lookup either way — while adding a key to store,
protect and rotate. A rotation would also silently orphan every token in every
saved session. Stability across restarts comes from the persisted map, which is
all that was ever required.

`tokens.json` is keyed by token because detokenization is the direction
correctness depends on: a wrong lookup shows one person's data in place of
another's. The value→token index is built in memory and never persisted.

### Each entry carries usage metadata

```json
"«email:692bf756»": {
  "type": "email",
  "value": "alex@example.com",
  "created": "2026-08-15T10:01:12Z",
  "last_used": "2026-08-15T10:04:38Z",
  "hits": 3
}
```

The three usage fields exist to make the map **prunable**. At `MAX_ENTRIES` an
operator has to decide what to drop, and without a last-used stamp the only
options are deleting the file — which strands every token in saved history as an
unresolvable placeholder — or keeping it forever. They also answer the question
the cap raises on its own: whether a full map holds live entities or addresses
the agent skimmed past once.

Nothing evicts automatically, deliberately. Dropping an entry makes its
placeholder unresolvable everywhere it was already written, including sessions
closed months ago, so which entries die is an operator's call and not a
heuristic's.

- **Minting is not a use.** A fresh entry is `hits: 0` with `last_used` seeded to
  `created`, so the field always answers "when was this last relevant" without a
  null case, and a minted-but-never-resolved entry is visible as such.
- **Writes are throttled, not written through.** Detokenization runs once per
  stream delta, so persisting every resolution would mean hundreds of rewrites of
  the plaintext map for one reply. The first resolution writes through and the
  burst behind it is absorbed for `_FLUSH_INTERVAL_SECONDS`; `AgentLoop.stop`
  flushes what is left. A crash costs at most one interval of *counters* — the
  mappings are already durable, because minting writes synchronously.
- **No schema bump.** These are metadata about a mapping rather than part of one,
  so a v1 file written before they existed stays readable: `_parse_entries`
  backfills the fields it cannot know. A missing `created` becomes
  `UNKNOWN_TIMESTAMP`, never the load time — backfilling "now" would make every
  old entry look freshly minted and destroy the signal the field exists to carry.

`_touch` reads `hits` through `.get` rather than by subscript because it sits on
the egress path that resolves placeholders for the user: an entry constructed
in-process without the usage keys must not turn a reply into a `KeyError`.

The map file is self-neutralizing when read back through a tool, which is what
makes the agent's reach into it survivable. An entry's `value` is the plaintext
keyed by its own token, so `cat tokens.json` passes through the tool-result hook
and comes back as `"value": "«email:d3522a34»"` — the file describes its own
contents in placeholder terms. This is a property of the hook, not of the file:
a map minted while tokenization was on stays plaintext on disk after it is
switched off, and nothing rewrites it then. Both directions are pinned in
`test_token_tool_results.py::TestMapFileIsSelfNeutralizing`.

`secrets.env` has no equivalent property — its values are not addresses, so no
hook matches them and a read returns them verbatim. That is the accepted risk
recorded above, not an oversight.

A corrupt map disables minting for the process rather than being overwritten: the
file may be recoverable by hand, and rewriting it would strand every token
already in saved history as an unresolvable placeholder.

### An explicit store must be compared with `is not None`, never truthiness

`TokenStore.__len__` exists, so an empty store is falsy, and the idiomatic
`store or DEFAULT_TOKEN_STORE` silently swapped a caller's store for the default
one for exactly as long as it was empty — the whole first-use window. Values went
into the operator's real `~/.atom/private/tokens.json` instead.

`__bool__` now returns `True` unconditionally, so the trap is closed at the class
rather than only at the two call sites; both sites also use `is not None`.

Found by `scripts/bench_privacy.py`, not by the suite: `conftest.py` redirects
`DEFAULT_TOKEN_STORE` for every test, so a test that *did* leak wrote to the
redirected map and passed. 4473 tests were green while the bug filled the real
map to `MAX_ENTRIES` with 9,948 synthetic addresses, which also capped real
tokenization. Anything that constructs a `TokenStore` outside pytest is on the
path the suite cannot see.

### The usage stamp is cached for the second it describes

`_utc_now` formats a second-resolution string, and `_touch` called it once per
resolution — so a reply carrying 200 placeholders formatted 200 identical stamps.
Measured: `strftime` 1.36us against 0.03us for the rest of `_touch` combined, 87%
of `detokenize`'s total cost. Caching it made egress 2.5-3.5x faster across the
ladder (Pi 44,932 -> 157,870 placeholders/second).

**Not a disk problem, which is worth recording because the obvious guess was
wrong twice.** The first hypothesis was that `_touch`'s throttled flush rewrote
the map during a burst; 100k resolutions wrote the map *zero* times. The cost was
never I/O — the same mistake as predicting fsync would dominate minting.

The cache is only correct because the stamp is deliberately second-resolution (see
the field docs above). It is module-level rather than per store, since the value
depends only on the clock, and unlocked: the worst a race does is have two threads
format the same second and assign the same tuple. `_stamp_cache` is lowercase
because it is reassigned — basedpyright rejects rebinding an uppercase name.

Minting benefited too (Pi bulk 22,725 -> 39,118/second): every new entry stamps
`created` and `last_used`, so it paid the same cost per value.

### Minting saves once per call, not once per value

`_save` reserializes the entire map, so saving per newly minted value made
minting *n* addresses write O(n²) bytes. Measured on APFS/NVMe, the fastest rung:
800 addresses took 926 ms and wrote 65 MB to produce a 160 KB file — 401×
amplification, ~3.6× per doubling. Reaching `MAX_ENTRIES` took ~130 s there and
~400 s on flash.

**The cost was serialization, not durability.** `json.dumps` with `indent=2` over
the whole map was ~99% of `_save`; the two `fsync` calls were 4%. That contradicts
the obvious guess — the first prediction was that fsync would dominate, and it
does not on any rung measured. It is also why `indent=2` was kept: dropping it
buys 2.3× alone and nothing on top of batching, and a readable file is what an
operator needs when pruning.

`tokenize` now wraps its substitutions in `TokenStore.minting_batch`. A bulk tool
result carrying 10,000 new addresses went from *not completing inside 45 s on any
rung* to 60 ms locally and 431 ms on the Pi.

Two properties the batch has to preserve, both easy to get wrong:

**The save happens before the caller sees the text.** It is inside the context
manager, not deferred past it. An `OSError` propagates and fails the call, because
by then every substitution has been made — returning the text would hand back
placeholders with no entry behind them and the addresses would be unrecoverable.
Failing is the deliberate choice for a feature that exists to prevent disclosure.

**The batch must not consume `_touch`'s first-use write-through.** `_last_flush`
starts at 0.0 so a process that resolves one token and exits still records it.
Restarting that timer at batch exit put the first resolution inside the throttle
window and broke two tests; the exit clears `_usage_dirty` (the save wrote the
counters too) but leaves the timer alone.

Nesting is counted rather than flagged, so an inner block cannot save early and
leave the outer one believing its entries are durable. The lock is held for the
whole block: minting already serialized per value, and letting one thread's exit
decide another thread's durability is an ordering bug that only appears under load.

`token_for` called directly, outside a batch, still saves immediately — unchanged.

**What batching cannot do:** collapse saves *across* calls. 10,000 addresses in
one call cost one save; the same addresses in 400 calls cost 400, and on the Pi
that is 431 ms versus 11.2 s. Both shapes are real (one bulk tool result versus a
stream of small ones), which is why `bench_privacy` reports `mint_rate_bulk_1s`
and `mint_rate_1s` separately rather than picking one. Cross-call coalescing is
the next lever if a chatty-MCP workload ever justifies it.

`MAX_ENTRIES` still exists for the disclosure argument above; it is no longer also
load-bearing for performance.

### `_EMAIL_RE` needs its lookbehind to stay linear

The local-part character class contains `.`, `-`, `+` and more, so a run of *n*
local-part-legal characters offers *n* candidate start offsets, each failing only
after scanning ahead to the `@`: quadratic in the run length. Tool output reaches
this trivially — base64 blobs and hex dumps are made of local-part-legal
characters, and one `@` anywhere past such a run defeats the cheap
`"@" not in text` exit.

Measured before the fix: 24 KB blob 343 ms, 40 KB 1.02 s. A `(?<![local])` guard
makes a match start only where the previous character could not have been part of
the local part — true of every real address, which is preceded by a space, quote,
bracket, or start of text. Same shape after the guard: 0.22 ms, a 1542×
improvement, with identical match results across 400k fuzzed strings and 29
targeted shapes.

The domain side has a nested quantifier that *looks* worse and is not: it is
anchored by literal dots, and measured linear.

### Every new pattern must declare a cheap marker gate

Written before a second pattern exists, because the structure has to be right
*first*: this cliff is the kind of thing found after three patterns ship, by which
point the design is already wrong.

`tokenize` returns immediately when `"@" not in text`. That guard is why
address-free text — almost every message and most tool output — costs nothing.
Measured on 35 KB of clean text:

| approach | cost |
| --- | --- |
| today, email only, early exit | 0.001 ms |
| 8 patterns, always scan | 3.482 ms |
| 8 patterns, one combined regex | 5.313 ms |
| 8 patterns, cheap marker prefilter | 0.638 ms |

Losing the early exit is a ~3,500× regression on the path every turn takes. So:
**a pattern declares a substring or character-class marker, and only runs when its
marker is present.** Phone numbers, IBANs and card numbers are the hard cases —
they have no distinctive literal — and each needs an explicit decision rather than
a default of "scan always".

**A combined alternation is not the answer**, which is worth stating because it is
the intuitive design. Measured *slower* than sequential passes on clean text
(5.3 ms vs 3.5 ms for 8 patterns): one large NFA with many branches costs more per
character than several cheap ones that mostly fail on their first byte.

Per-pattern ingress cost is otherwise unremarkable — 0.4–0.7 ms each over 39 KB,
roughly additive.

**Egress already scales for free.** `_TOKEN_RE` is `«([a-z]+):([0-9a-f]{8})»` — the
pattern count does not appear in it, so `detokenize` is independent of how many
entity types exist. That is a property of the placeholder format, and a reason not
to change it casually.

### The map is capped, and the cap loses data rather than leaking it

`MAX_ENTRIES = 10_000`. Tool output is the reason: one `grep -r "@"` over a mail
directory can carry thousands of addresses, and the map is append-only, so an
uncapped map grows from data the agent merely passed over.

At the cap, new values become `«email:capped»` — deliberately **not** resolvable.
Leaving the plaintext in place instead would mean a size limit silently turns
into a disclosure, which is the one outcome this feature exists to prevent. Data
is lost rather than leaked, and the operator is told once (not per value, or a
single bulk result would emit thousands of identical lines).

Addresses already in the map keep resolving when the cap is reached.

### Splitting an address into parts was considered and deferred

`«user:…»@«domain:…»` would hide the domain too and shrink the map — one entry per
domain instead of one per address — while still letting the agent group and
filter by domain. It was half-built and then removed: the naming was unsettled
(`local` vs `user` vs `alias`), and an entry `type` is written to disk, so
renaming one later means migrating live data. Revisit from a clean slate.

The cost of not having it: an agent cannot filter tool output by domain, because
the domain is inside the placeholder. Grouping still works — two identical
placeholders are the same person — and the runtime context block says so.

### The model must be told

`provide_token_runtime_context` explains placeholders every turn. Without it the
model asks the user for an address they just supplied, "corrects" a placeholder
to an invented one, or tells the user about the placeholder instead of using it.
Registered only when tokenization is on.

### Streaming is the normal path, and it broke first

A model emits a placeholder as several deltas (`«`, `email`, `:`, …), so
resolving each delta in isolation finds nothing and the user sees a raw
placeholder. `PlaceholderStreamResolver` holds back a tail that could be the
start of a placeholder and releases it when the placeholder completes, or at
stream end. `MAX_HELD_CHARS` bounds the hold so a stray `«` in prose cannot
stall a stream.

Found by running the real agent. The unit tests passed throughout — they fed
whole placeholders, which is the one case streaming never produces.

### Four egress boundaries, not one

`MessageBus.publish_outbound` covers everything reaching a channel through the
bus. `process_direct` (used by `atom agent -m`, the API server, subagents)
returns its result to the caller and drives its own callbacks, so it needs the
same treatment in three more places:

| Path | Resolved by |
| --- | --- |
| bus (`OutboundMessage`, stream deltas, progress events) | `MessageBus.filter_outbound` |
| direct return value | `_resolve_outbound_placeholders` |
| direct stream callbacks | `_resolve_direct_stream_callbacks` |
| direct progress callback | `_resolve_direct_progress_callback` |

All four route through the bus's own filter, so there is one definition of what
gets resolved. The progress case was found live: a tool-hint line showed
`echo 'contact is «email:ed07eada»'` while the reply beside it showed the
address.

## What is implemented: declared masks

`/mask name Alexey` registers a literal value as personal data. From then on it is
replaced with `«name:a1b2c3d4»` everywhere tokenization runs — message text, tool
output, subagent results, transcription — and resolved back on the way out to the
user, exactly like an address.

### Declared, not discovered — and the distinction is the design

An address is found by `_EMAIL_RE` whether or not anyone registered it. There is
no equivalent pattern for a person's name: attempting one is NER by regex, and it
would rewrite ordinary words forever. **The command is the detection.** Nothing is
inferred, which is why `/mask` requires the operator to say what the value is.

Consequence worth stating plainly: a mask only applies *after* it is registered.
Text already in session history keeps the plaintext. Retroactive masking would
mean rewriting saved history, which is a much larger decision and was not taken.

### Typed, not one generic `text`

`TYPE_NAME`, `TYPE_SURNAME`, `TYPE_ADDRESS`, `TYPE_PHONE`, `TYPE_COMPANY`, plus
`TYPE_TEXT` as an escape hatch for a value that fits nothing else.

The type is the only thing the model sees. `«text:a1b2c3d4» sent an invoice to
«text:99887766»` leaves it unable to tell a person from a company, so it cannot
choose "they" over "it" or write grammatical prose around the placeholder — and
`_TOKEN_GUIDANCE` exists precisely because it misbehaves when it cannot tell. A
line naming each type and its meaning is appended to that guidance for the same
reason: a label the model has no explanation for is a label it ignores.

Resolution is type-agnostic (`_TOKEN_RE` matches `[a-z]+`), so the label costs
nothing structurally. Two constraints follow from that regex, both verified: a type
must be a single lowercase word — `first_name`, `firstName` and `given-name` are
**not resolvable** — and renaming a type means migrating live data, since it is
written to disk inside every placeholder. `TYPE_TEXT` is deliberately *alongside*
the specific types rather than a default; as a default it would quietly become the
common case and give back the ambiguity the specific types remove.

`MASK_TYPES` is a closed set, not a free-form field, so `/mask nmae` is refused
rather than minting a type nothing has guidance for.

### The hazard is corrupting text, not performance

A wrong substitution is worse than no substitution: the agent reasons over what
comes back. Three rules, each from a measured failure:

- **`\b` on both ends.** Without it, masking `Sage` turns `Sagebrush` into
  `«name:…»brush`.
- **`MIN_MASK_LENGTH = 4`.** Masking `An` rewrites "an update" and `Read` rewrites
  "please read" — both matched ordinary prose twice in a single test sentence.
  This is the one rule an operator will hit and dislike, and it is not negotiable
  for exactly that reason.
- **Longest value first.** With `Sage` and `Sage Smith` both registered, matching
  in insertion order yields `«name:…» Smith` and the surname survives in
  plaintext.

Also refused: a value with no letter or digit (nothing for `\b` to anchor to) and
one containing guillemets (they delimit placeholders).

Case is folded, so `alexey`, `Alexey` and `ALEXEY` share one token — the same
choice `canonical_email` makes, and for the same reason: two tokens for one person
is the worse error. Resolution shows the *registered* spelling back, which is
deliberately lossy on display.

### Cost is near-flat in the size of the registry, and the miss path is not the whole story

One alternation over every declared literal, cached against the mask set so a
`/mask` mid-session takes effect on the next message without recompiling per call.
Over 35KB of text containing **no** mask: 0.23ms for 1 mask, 0.23ms for 100, 0.26ms
for 1000 — the engine prefilters the alternation. An operator who never runs `/mask`
pays one attribute lookup, since `mask_pattern()` returns `None`.

That measurement covers only the **miss** path, and taking it as the whole answer
hid a defect. On a **hit**, `token_for_mask()` resolves the matched substring to a
token, and it originally did so by iterating the entire map and casefolding every
entry. It cannot use `_by_value`, which is keyed by `(type, exact value)`: the
alternation that found the text carries neither the type nor the registered casing.
So the cost was O(masks × hits), and ingress over 400 masked occurrences fell from
2,108,370 hits/s at 1 mask to 113,469 at 1000.

`_by_folded_mask` — casefolded value to token, rebuilt in `_load()` beside
`_by_value` and maintained in `token_for()` and `remove_mask()` — turns that into
one dict lookup: 996,056 hits/s at 1000 masks, 8.8× better. The slope that remains
is the 1000-branch alternation itself (0.115ms → 0.290ms for the regex alone), not
the lookup, which measures ~0.16µs per hit.

Egress was flat throughout and needed no change: detokenization is keyed by the
placeholder, so it is a dict hit regardless of registry or map size — ~1.8–1.9M
tokens/s across registry sizes 1–1000 and map sizes 10–5000.

The lesson generalizes: **benchmark the hit path separately from the miss path.**
A prefilter that returns early makes the miss path flat by construction and says
nothing about what happens when it matches.

This satisfies the marker-gate rule above: the gate is the alternation's own
prefilter. `bench_privacy` covers it with `mask_registry_{1,100,1000}` and
`mask_hits_100`; `TestMaskLookupIsIndexed` pins the index, including the two places
it is maintained by hand.

### Removal strands existing placeholders, and says so

`/mask del <value>` deletes the entry. Every placeholder for it already written
into saved history stops resolving. That is the honest outcome — the alternative
is keeping the plaintext on disk after an operator asked for it to be forgotten —
and the reply states it rather than reporting plain success.

### The command surface follows `/secrets`

Priority tier, both spellings (`/mask`, `/masks`), because the arguments carry the
value in plaintext and that tier is the path which persists nothing to session
history and sends nothing to the model. Replies never quote the value, including
on refusal, and every path that saw a value sets `carried_value` so the channel
deletes the user's message. `/mask` with no arguments lists types and lengths, never
values: printing them would put every masked name back into the chat at once.

## Modules

| File | Owns |
| --- | --- |
| `store.py` | `secrets.env` — validation, parsing, atomic writes |
| `commands.py` | `/secrets` reply text and the delete-source request |
| `mask_commands.py` | `/mask` reply text, listing, and the delete-source request |
| `env.py` | injection into `ExecTool._build_env` |
| `tokens.py` | `tokens.json` — minting, both-direction lookup, `tokenize`/`detokenize`, the mask registry |
| `hooks.py` | the ingress boundaries and the model-facing guidance block |
| `stream.py` | placeholder resolution across stream-delta boundaries |

Testing note: `conftest.py` redirects both `DEFAULT_SECRET_STORE` and
`DEFAULT_TOKEN_STORE` for every test in the suite. `_build_env` reads the secret
store and `tokenize_emails` defaults on, so without these a test would read — and
a mistakenly un-injected write would modify — the developer's real
`~/.atom/private/` files. An early version of the command tests did exactly that.

Unit tests are not sufficient for this feature. Three of its bugs only appear
against a live model: the direct-mode return value, split stream deltas, and the
progress line. Run `atom agent -m` against a real provider before trusting a
change here.
