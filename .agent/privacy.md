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

## Modules

| File | Owns |
| --- | --- |
| `store.py` | `secrets.env` — validation, parsing, atomic writes |
| `commands.py` | `/secrets` reply text and the delete-source request |
| `env.py` | injection into `ExecTool._build_env` |
| `tokens.py` | `tokens.json` — minting, both-direction lookup, `tokenize`/`detokenize` |
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
