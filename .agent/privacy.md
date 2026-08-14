# Privacy: Secret Store

Design record for `atom/privacy/`. Records the decisions behind the shipped
behaviour and the ones deliberately deferred, so neither gets re-litigated or
silently reversed.

## What is implemented

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
```

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

## Known gaps

- **The chat channel sees the value.** Typing `/secrets set` in Telegram puts
  the plaintext in the sender's message history and on Telegram's servers before
  atom ever sees it. Priority dispatch protects atom's disk; it cannot unsend
  that. The reply advises deleting the message. This risk was explicitly
  accepted; the CLI avoids it.
- **No authorization.** The router does not restrict who may run a command, so
  in a group chat any member can set a secret that the shell tool then inherits.
  Gating on the pairing store, or restricting to DMs, is unbuilt.
- **Cross-process writes are last-write-wins.** The in-process lock serializes
  atom's own access; the CLI running against a live gateway is not coordinated.
  The write itself is atomic, so the file is never torn.
- **Values are readable by anything running as the user.** 0600 is filesystem
  permissions, not encryption.

## Deferred: tokenization

Reversible substitution of PII (`alex@example.com` → a stable placeholder) was
designed alongside this and is **not** built. It is a materially different
feature: unbounded discovered values rather than operator-declared ones, a
persisted map, and a per-tool detokenization allowlist that is where the whole
risk sits. If it is picked up, note that it is *pseudonymization*, not
anonymization — the map re-identifies every entity, so the data stays in scope
under GDPR. Do not name a config key, command, or docstring `anonymize`.

## Modules

| File | Owns |
| --- | --- |
| `store.py` | `secrets.env` — validation, parsing, atomic writes |
| `commands.py` | `/secrets` reply text |
| `env.py` | injection into `ExecTool._build_env` |

Testing note: `conftest.py` redirects `DEFAULT_SECRET_STORE` for every test in
the suite. `_build_env` reads the store, so without it any test that builds a
subprocess environment would read — and a mistakenly un-injected write would
modify — the developer's real `secrets.env`. An early version of the command
tests did exactly that.
