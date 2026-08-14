# Versioning

atom is versioned `X.Y.Z`. Exactly one component moves per release, and it always
moves by one. `pyproject.toml` holds the only copy — `atom.__version__` reads
installed dist metadata and falls back to that file.

Never hand-edit the version. Run:

```bash
python -m scripts.bump_version patch     # 0.3.0 -> 0.3.1
python -m scripts.bump_version minor     # 0.3.1 -> 0.4.0
python -m scripts.bump_version major     # 0.4.0 -> 1.0.0
python -m scripts.bump_version --check   # print the current version, write nothing
```

The script refuses multi-step jumps and backwards moves. Skipping ahead
(`0.3.0` → `0.5.0`) implies a release that never existed; going backwards makes
an older artifact outrank a newer one.

## Which component

Decide from the perspective of somebody already running atom who upgrades without
reading anything. Pick the **highest** row that applies.

| Bump | When | Examples |
|---|---|---|
| `major` | A working setup breaks until the operator changes something | A config key is removed or renamed with no fallback; a CLI command or flag is deleted; a provider or channel is dropped; a tool's name changes; on-disk session or memory layout changes without a migration; the minimum Python version rises |
| `minor` | New capability, existing setups keep working | A new tool, channel, provider, or CLI command; a new config field with a default; a new SDK or HTTP surface; a new config key that supersedes an old one *while the old one still works* |
| `patch` | Behavior the operator already had gets more correct | Bug fix; a crash or hang fixed; performance; docs; internal refactor with no visible change; dependency bump that changes nothing observable; branding and asset changes |

Since atom is pre-1.0, `major` means `0.x` → `1.0.0`. That is a real decision
about the project, not a mechanical consequence of one breaking change. Until
`1.0.0` exists, route breaking changes to `minor` and say plainly in the commit
body what breaks and what the operator must do. Ask before bumping `major`.

## Ambiguity

- **A bug fix that changes documented behavior an operator relies on** — `minor`,
  not `patch`. The upgrade is safe, but the observable change deserves to be
  visible in the version.
- **A new field plus a fix in one change** — `minor`. The highest row wins.
- **Anything invisible to the operator** (tests, CI, `.agent/` docs, comments,
  contributor scripts) — `patch`. It changes nothing an operator can see, but
  every pushed commit still gets a version, so `atom --version` always
  identifies exactly one tree. When in doubt between "no bump" and `patch`,
  pick `patch`.

## Releasing

The bump, the commit, and the tag are one unit. A tag that points at a commit
whose `pyproject.toml` says something else is worse than no tag.

```bash
python -m scripts.bump_version <component>
# stage the version bump together with the change it releases
git commit -m "<what changed>"
git tag -a "v$(python -m scripts.bump_version --check)" -m "atom v$(python -m scripts.bump_version --check)"
git push origin main --follow-tags
```

`--follow-tags` pushes annotated tags reachable from the commits being pushed, so
the tag cannot be left behind locally. Only tag from `main`, and only when the
tree is clean and the checks below pass.

## Before pushing

Run these; all three must be clean, since a tagged commit is the one people
install from:

```bash
uv run --no-sync basedpyright     # 0 errors, 0 warnings, 0 notes
.venv/bin/ruff check atom/
.venv/bin/python -m pytest -q
```
