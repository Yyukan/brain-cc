# Contributor guide

Plugin: persistent cross-session memory for Claude Code. Design: `docs/spec.md`.

## Hard constraints

- Global only. Never read or write inside a project repo or a user's own
  instructions file. State lives in `~/.agents/brain/` plus the installed
  skill/hooks under `~/.claude`.
- Python 3 stdlib only, tests included. No third-party packages.
- Installer: skill + hooks are code (always overwrite); the store is data
  (scaffold only if missing, never touch an existing one). Never `rm -rf`
  through a symlink; refuse a foreign skill dir unless `BRAIN_FORCE=1`.
- Session-end hook stays fast: cheap checks, then a fully detached worker.
- Recursion guard: `BRAIN_HEADLESS=1` makes both hooks exit silently first.
- `state.json.enabled` is checked first; missing/unparsable means disabled.
- Every generated content file is OKF (frontmatter, bundle-relative links,
  reserved `index.md`/`log.md`); `short-memory.md` is flat, <=100 lines x
  <=200 chars. `SKILL.md` follows Claude Code's skill format instead.
- Merge, don't append, in content pages.
- No empty stubs: create files/folders only when there is material.

## Script header convention

Every script opens with a comment block: `WHY` (why it exists), `WHAT`
(2-3 lines), `WHO` (hook, script, skill or human that invokes it).

## Testing safely

Never run installer, hooks or scripts against the real home. Always set
`AGENT_HOME=<tmp> CLAUDE_CONFIG_DIR=<tmp>`. Run
`python3 -m unittest discover -s tests`. Live tests (real `claude`) run only
with `BRAIN_LIVE_TESTS=1`. `BRAIN_DIGEST_TIMEOUT` shortens the digest timeout.

## Not live until reinstalled

Edits in this repo do nothing for the running Claude Code until
`./install.sh` copies them to `~/.claude/skills/brain` and merges hooks.

## Keep in sync

Layout changes must update `doctor` (brain_cli.py), `docs/spec.md` and
`README.md` in the same change.

## Git

Stage new files (`git add`), never commit or push unless explicitly asked.
