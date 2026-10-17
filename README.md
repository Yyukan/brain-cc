# second-brain (Claude Code plugin)

Persistent memory across clean Claude Code sessions, built only from a skill,
two lifecycle hooks and plain markdown files. No database, no service, no API
keys, Python 3 stdlib only.

## Why

Every new session starts from zero: no decisions, project facts or
preferences from yesterday. This plugin rebuilds that memory on top of what
Claude Code always has.

## What it does

- **Session start hook** injects the rules (`references/instructions.md`) and
  `short-memory.md` into the session.
- **Session end hook** does a cheap check, then spawns a detached worker that
  captures the transcript into `inbox/` and runs a headless `claude -p`
  digest, which folds the inbox into the long-term memory.
- **Long memory** is an OKF bundle (markdown + YAML frontmatter) under
  `long-memory/`, loaded on demand via `index.md` / grep.
- **`/brain` skill** controls it all.

## Install

```
./install.sh
```

Safe to re-run: skill and hooks are overwritten (code), the store at
`~/.agents/brain/` is scaffolded only if missing (data). Hooks are merged
into `~/.claude/settings.json` (existing entries are kept, backup `.bak`
made once). It refuses to overwrite a foreign `~/.claude/skills/brain`
unless `BRAIN_FORCE=1`.

## Usage

| Command | What |
| --- | --- |
| `/brain on` / `off` | Enable / disable (hooks become no-ops) |
| `/brain status` | Enabled flag, inbox / short / long sizes |
| `/brain init N` | Backfill the last N past sessions into `inbox/`, then offer a digest |
| `/brain digest` | Digest the inbox now, inline, with the current session's model |
| `/brain digest model [id]` | Get / set the model of the automatic background digest (`auto` = default) |
| `/brain digest notification [on\|off]` | Get / set the macOS notification at the end of the background digest (default on): created/updated pages, short-memory additions, inbox size; `✗` on failure/timeout |
| `/brain doctor` | Pass/fail health check of install, hooks, store and OKF validity |
| `/brain dashboard` | Generate and open a read-only static HTML overview |

### Dashboard

`/brain dashboard` (or `python3 ~/.claude/skills/brain/scripts/brain_cli.py dashboard [--no-open] [--out PATH]`)
writes `~/.agents/brain/dashboard.html` and opens it: short/long memory
sizes, growth over time (from `metrics.jsonl`), and a graph of pages, tags and
links. It is a static snapshot - read-only, no server, no editing; re-run to
refresh (the page does not refresh itself; re-run `/brain dashboard`). It works while the brain is off (shows a disabled banner).

## Layout

```
install.sh
hooks/brain.json                  hook wiring (merged into settings.json)
skills/brain/                     installed to ~/.claude/skills/brain
  SKILL.md                        /brain dispatch
  .brain-plugin                   ownership marker
  config/state.json               default state
  references/instructions.md      injected every session
  references/digest.md            digest cookbook (inline + headless)
  scripts/                        session_start, session_end, digest_runner,
                                  capture, brain_cli, brain_lib, dashboard
  assets/dashboard_template.html
tests/                            stdlib unittest
docs/spec.md                      design spec
~/.agents/brain/                  the store (created once)
  state.json  brain.log  .sessions  metrics.jsonl  short-memory.md
  inbox/  long-memory/{index.md,log.md,...}  dashboard.html
```

## Testing

Never test against your real home. Use temp dirs:

```
export AGENT_HOME=$(mktemp -d) CLAUDE_CONFIG_DIR=$(mktemp -d)
./install.sh
python3 -m unittest discover -s tests
```

`AGENT_HOME` moves the store (`$AGENT_HOME/brain`), `CLAUDE_CONFIG_DIR` moves
the skill, `settings.json` and transcripts. `BRAIN_CLAUDE_BIN` points the
digest at a fake `claude`. `BRAIN_DIGEST_TIMEOUT` sets the digest timeout in
seconds (default 900; exit 124 on timeout). A live layer of tests runs only
with `BRAIN_LIVE_TESTS=1`.

## Limitations

- No secret redaction or client isolation: raw transcripts land in one global
  store. Compliance risk on client work.
- No lock: two digests at once race (last write wins).
- No rollback: captures are deleted after digest; `doctor` detects structural
  corruption but cannot restore facts lost by a bad merge.
- Promotion to short memory is model judgment; a fact may flip in and out.
- Dashboard is included but read-only static HTML (no editing, no live data).
- The headless digest has no `rm`: the runner deletes the inbox files itself
  after `claude` exits 0, so it cannot delete or move long-memory pages.
  Stale or orphaned pages can remain; `doctor` flags them only if `index.md` no longer lists them, fix via inline
  `/brain digest`.
- Resumed sessions that skip SessionStart are not digested.
- Single user; multi-agent orchestration is out of scope.
