---
name: brain
description: Persistent second-brain memory layer across sessions. Use when the user runs /brain, or asks to turn memory on/off, check status, extract past sessions, digest inbox notes into long-term memory, run the brain health check, or open the brain dashboard.
argument-hint: "[on|off|status|init N|digest|digest model [id]|doctor|dashboard]"
---

# Brain

On/off switch, status, session extraction, digest trigger, health check and
dashboard for the second-brain memory layer (store: `~/.agents/brain/`).

## How It Works

Get the **command text**: use `$ARGUMENTS` if it is non-empty. Otherwise find
the literal substring `/brain` in the invoking message, take everything after
it, and trim leading/trailing whitespace.

Then look at the first word of the command text and dispatch. Do not guess,
do not default to `status` if unsure. If the first word is not one of these
(or the command text is empty), show the Commands table below and stop:

| First word of command text | Action |
| --- | --- |
| `on` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py on`, show output verbatim. |
| `off` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py off`, show output verbatim. |
| `status` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py status`, show output verbatim. |
| `init` (`init N`) | N = the number after `init` (must be a positive integer, else show the Commands table and stop). Run `python3 ~/.claude/skills/brain/scripts/capture.py N`, show its report table verbatim (id, date, turns, size, file), then ask the user whether to run `/brain digest` now. |
| `digest` alone | **Do not run a script.** Read `~/.claude/skills/brain/references/digest.md` and follow it yourself, inline, with your own file tools (Read, Grep, Write, Edit) - the same cookbook the automatic digest runs, but with the model of this session. Store root is `~/.agents/brain/` (or `$AGENT_HOME/brain` if that env var is set; check with `echo $AGENT_HOME`). When finished, run `python3 ~/.claude/skills/brain/scripts/brain_cli.py metrics` and show its output, then give the cookbook's summary. Skip the metrics call if the cookbook stopped with "inbox empty". |
| `digest model` (no id) | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py digest model`, show output verbatim. |
| `digest model <model-id>` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py digest model <model-id>`, show output verbatim. |
| `doctor` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py doctor`, show output verbatim. |
| `dashboard` | Run `python3 ~/.claude/skills/brain/scripts/brain_cli.py dashboard`, show output verbatim (it prints the path of the generated read-only HTML and opens it in the browser). |

Paths above assume the default Claude config dir (`~/.claude`); a custom
`CLAUDE_CONFIG_DIR` install is for testing only.

If a script exits non-zero, show its output and the exit code; do not retry
or work around it.

## Commands

| Command | Purpose |
| ------- | ------- |
| `/brain on` | Sets `state.json.enabled = true` |
| `/brain off` | Sets `state.json.enabled = false` - hooks become no-ops |
| `/brain status` | Shows enabled state, inbox files and sizes, short memory size, long memory size |
| `/brain init N` | Extracts the last N sessions into `inbox/`, prints the report, then offers `/brain digest` |
| `/brain digest` | Runs the digest cookbook inline, right now, then records a metrics snapshot |
| `/brain digest model` | Prints the current `state.json.digest_model` |
| `/brain digest model [model-id]` | Sets `state.json.digest_model` - model the automatic background digest uses (`auto` = host default) |
| `/brain doctor` | Deterministic health check: skill install, hooks wiring, store structure, OKF validity, short-memory caps - pass/fail per check |
| `/brain dashboard` | Generates a read-only static HTML overview of the brain (memory sizes, growth, link graph) and opens it |
