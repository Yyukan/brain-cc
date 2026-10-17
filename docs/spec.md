# Cross-Agent "Brain" Plugin — Abstract Specification

An agent-agnostic design for giving any coding agent (GitHub Copilot CLI, Claude Code, Cursor, Aider, a custom agent harness, etc.) **persistent memory across clean sessions**, using only primitives that are *commonly* available across such tools: a **skill/command surface**, **lifecycle hooks**, and **plain files on disk**. No database, no external service, no API keys, and no dependency on any one vendor's memory/MCP feature — this is the fallback design for when that feature doesn't exist or is disabled.

This document is deliberately **abstract**. Every concrete agent has its own skill format, hook contract, and config location — §13 gives per-agent adaptation notes, but the actual wiring for "agent X" must be written against agent X's own docs. What's reusable across all of them is the *shape* of the solution: sections marked **[generic pattern]** are that reusable shape; anything else is an illustrative example, not a requirement.

---

## 1. Problem this plugin class solves [generic pattern]

Every fresh agent session starts from zero: no memory of prior conversations, decisions, project facts, or user preferences — even when the same human keeps working with the same agent on the same projects day after day. Native "memory" features (if the agent/host provides one) may be disabled by policy, unavailable in a given environment, or simply not exist for a given tool. A plugin in this class rebuilds persistent memory on top of whatever the agent *does* support everywhere:

1. **A skill or command** — a user-facing control surface, invoked explicitly (e.g. a slash command) or made discoverable to the agent via its normal skill/tool-discovery mechanism.
2. **Lifecycle hooks** — code the host runs automatically at defined points in a session's life (at minimum: *session start*, *session end*; some hosts offer more granularity). Hooks are the only thing that makes memory *passive* — without them, the user would have to manually ask the agent to "load" and "save" memory every time.
3. **Plain files as the store** — markdown/JSON/whatever the chosen agent can read and write with its ordinary file tools, living in one well-known, agent-scoped location, never inside a project/client repo.

---

## 2. Hard constraints to carry into any plugin of this class [generic pattern]

- **Global only, never project-scoped.** Nothing reads or writes inside a repo the agent happens to be working in; nothing reads or writes any project- or user-authored instructions file the agent already uses for its own purposes (e.g. an existing custom-instructions file). All plugin state lives in one dedicated location outside any project — e.g. `~/.agents/brain/` as a host-agnostic default — plus wherever the host expects installed skills/hooks to live.
- **Runtime = one interpreter, stdlib only.** Pick a single language runtime guaranteed present wherever this will run (commonly Python 3 or Node.js) and write every script against its standard library only — no third-party packages. This keeps the installer a straight file copy with no dependency-resolution step.
- **Idempotent, non-destructive installer.** Skill and hook definitions are *code* → always safe to overwrite on reinstall. The runtime **store** is *data* → scaffold it only if missing; never touch an existing store on reinstall. Support an environment-variable override of the install root for testing.
- **Hook time budget.** Many hosts kill "end of session" hooks on a short, sometimes undocumented timeout, regardless of any configured timeout value. Any hook that needs to do slow or paid work (e.g. an LLM call) must: do only cheap, synchronous checks in the hook itself, then **spawn a fully detached child process** and return immediately. The child does the real work on its own, independent timeout.
- **Recursion guard.** If the detached child itself invokes the agent again (e.g. to run a summarization prompt), that nested invocation will fire the *same* hooks a second time. Pass a sentinel environment variable that **both** the session-start and session-end hooks check first and no-op on, before any other logic — the headless run must get neither injected memory nor a follow-up digest.
- **Enable/disable flag.** A single config file with at least `enabled: bool` that every hook checks first; `false` means the hook does nothing (silent no-op — whatever "no output" means under the host's own hook contract).
- **Structured knowledge format for every generated file.** Long memory is a strict **OKF v0.1** (Open Knowledge Format) bundle: markdown concept files with YAML frontmatter, OKF cross-link rules and reserved filenames, so pages stay self-describing, greppable and portable to other OKF tooling. Apply it to every file the plugin writes. The skill/command definition file itself is typically the one exception — it must follow the *host's* own skill-file format, not your content format.
- **Script header convention.** Every script opens with a short `WHY` / `WHAT` / `WHO` comment block (why it exists, what it does in 2–3 lines, who invokes it — a hook, another script, the skill/command, or a human running it manually). Keeps a dependency-free script tree self-documenting without external docs.
- **No empty stubs.** Generated content files/folders are created only when there is real material for them — never scaffold empty placeholder pages "just in case".
- **Merge, don't append,** wherever a process re-touches a generated file — a background/automatic process that only grows a file forever is a bug, not a feature.

---

## 3. Repository layout [generic pattern]

```
<plugin-repo>/
├── AGENTS.md / CONTRIBUTING.md   # contributor guide (constraints, conventions, "not live until reinstalled")
├── README.md                     # human-facing: why, what it does, install, usage, layout
├── install.sh                    # the only install entry point (see §4)
├── docs/
│   └── plan.md                   # authoritative design doc: goal, constraints, full layout, "as built" log
├── hooks/
│   └── <plugin>.<ext>            # hook wiring in whatever format the target host expects
└── skills/<plugin>/              # or whatever the host calls its skill/command bundle unit
    ├── <skill-definition-file>   # command dispatch surface, in the host's own skill format
    ├── config/
    │   └── state.json            # canonical DEFAULT state — copied verbatim into the store on first install
    ├── references/
    │   ├── instructions.md       # plain-text rules injected at session start (no frontmatter)
    │   └── <cookbook>.md         # any "do this procedure yourself with your own tools" cookbook
    └── scripts/
        ├── session_start.*       # session-start hook entry point
        ├── session_end.*         # session-end hook entry point (fast path only)
        ├── <worker>_runner.*     # detached child spawned by session_end for slow/paid work
        ├── <capture>.*           # pulls raw session material into the store (manual + automatic entry point)
        └── <plugin>_cli.*        # on/off/status/config/doctor — every skill subcommand that isn't a hook
```

Installed copy (what `install.sh` produces on a machine — paths below use a host-agnostic placeholder root; substitute the real host-specific locations for skills/hooks, but keep the **store** itself host-agnostic):

```
<host skill install root>/<plugin>/   # verbatim copy of skills/<plugin>/ above, scripts made executable
<host hook install root>/<plugin>.*   # verbatim copy of hooks/<plugin>.*

~/.agents/brain/                      # the runtime store - created ONCE, never overwritten after
├── state.json                        # copied from config/state.json on first install only
├── <plugin>.log                      # append-only log every script writes to (hook calls, worker runs, errors)
├── .sessions                         # bookkeeping file(s) specific to the plugin's own logic
├── <short-term-file>.md              # small, hard-capped, injected every session
└── <long-term-tree>/                 # everything else, loaded on demand, never injected wholesale
```

Using one store location (`~/.agents/brain/`) regardless of which agent is running means the *memory itself* can in principle be shared or at least coexist across multiple agents on the same machine, even though each agent's skill/hook wiring is installed separately in that agent's own host-specific location. Whether sharing is actually desirable depends on the agents involved — see §13.

---

## 4. `install.sh` contract [generic pattern]

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_HOME="${AGENT_HOME:-$HOME/.agents}"     # or whatever root the target host actually uses
STORE_ROOT="$AGENT_HOME/brain"

# 1. skill/command definition: ALWAYS overwrite (rm -rf then copy; chmod +x scripts)
# 2. hooks: ALWAYS overwrite (plain copy, in the host's expected format/location)
# 3. store: create skeleton ONLY if missing (mkdir -p subfolders, copy
#    config/state.json verbatim, write minimal seed files with an epoch
#    placeholder `generated.at` timestamp) - if it exists, print
#    "already exists, leaving it untouched" and do nothing else.
```

Key properties to replicate: single command, safe to re-run, install root overridable via an environment variable for tests, prints one line per step so a user can see exactly what happened.

---

## 5. Hook wiring [generic pattern]

Every host that supports lifecycle hooks exposes them differently (a JSON config file, a settings block, a plugin manifest, a code callback registered at startup...). Whatever the concrete mechanism, the plugin needs, at minimum, two events wired to two scripts:

- **session start** → runs a script that can inject context (rules + short-term memory) into the agent's next turn.
- **session end** → runs a script that does cheap checks, then hands off slow work to a detached worker.

Abstract shape, independent of the host's actual config syntax:

```
on sessionStart: run session_start.*, timeout ~10s
on sessionEnd:   run session_end.*,   timeout ~10s
```

**Hook stdio/contract properties to expect, in some form, from any host:**

- The hook receives some identifying payload for the event (at minimum, a session identifier; session-end payloads typically also carry a reason/cause for the event).
- The hook's only output, if the host supports hooks contributing context, is some structured object the host parses to inject into the agent's context (e.g. a single JSON object with a context-string field). No other output should be emitted on that channel; when disabled or no-op, emit *nothing* and exit cleanly.
- The host may enforce an aggressive, possibly-undocumented wall-clock budget on session-end hooks irrespective of any configured timeout value — never rely on the configured timeout being honored; see §2's detach-on-slow-work requirement.

---

## 6. The skill/command definition [generic pattern]

Whatever the host's own skill/command format is, the body should follow an **explicit parse-and-dispatch algorithm**, not vague guidance, so the agent's behavior invoking it is deterministic:

1. State exactly how to extract the "command text" from the invoking message (e.g.: find the literal command trigger, take everything after it, trim whitespace).
2. A dispatch table: first word of command text → exact action. Each row says whether to (a) run a script and show its output verbatim, or (b) follow a reference cookbook inline using the agent's own tools (no script) — use (b) only for steps that need live judgment the agent must apply, use (a) for anything deterministic/scriptable (state flips, status, health checks).
3. Explicit instruction: if the first word doesn't match any row, show the command table and stop — never guess, never default to one subcommand.
4. A second "Commands" table restating each command's purpose in one line, for human-readable `--help`-style display.

---

## 7. Config (`state.json`) [generic pattern]

Minimal, flat config, one canonical default copy shipped with the plugin, one live copy in the store root:

```json
{ "enabled": true, "digest_model": "auto" }
```

Optional `"digest_notification": bool` (missing = `true`): kill switch for the digest runner's end-of-run desktop notification (§8.3). If present it must be a bool (doctor flags anything else).

Rules: every hook and every subcommand reads this file fresh each invocation (no caching across processes — each run is a fresh process). Missing file or unparsable content ⇒ treat as disabled (fail closed, not open).

---

## 8. Scripts — responsibilities, in invocation order [generic pattern]

### 8.1 `session_start.*` — lifecycle hook, cheap, synchronous

- WHY: inject rules + durable short-term state into every new session without touching any instructions file the user already owns.
- Reads the event payload, logs it. Checks, in order: recursion-guard environment variable → `state.json.enabled`.
- If enabled: records any bookkeeping the end-of-session hook will need later to distinguish a genuine session from a spurious one (see 8.2), and emits the injected-context payload: rules/instructions file + short-term memory file, concatenated.
- If disabled or store missing: emits nothing, exits cleanly. No error output ever on the happy "disabled" path.

### 8.2 `session_end.*` — lifecycle hook, must stay under the shutdown budget

- WHY: real work (e.g. a summarization/LLM call) cannot run inside the hook's own process — the host may kill it too soon.
- Reads the event payload, logs it. Checks, in order: recursion-guard environment variable → `enabled` → a domain-specific "should I actually act on this event" predicate. For the brain that predicate is only: skip sessions with **zero user turns**. Any session with at least one turn is digested; the model judges whether it holds durable facts (short sessions can be valuable).
- **Spurious-event filtering (a pattern worth generalizing, not a specific bug):** some hosts fire a session-end-shaped event for sessions that never really started in the user-visible sense (e.g. a resume/bootstrap flow). Guard against acting on these by requiring the session id to have been recorded by the session-start hook first. If a host is known to skip session-start for some session type entirely (e.g. "resumed" sessions), document that as an accepted limitation rather than silently pretending it's handled.
- If all checks pass: resolves wherever progress should be mirrored back to the user (if the host supports it) and spawns the worker fully detached (independent stdio, independent process group), passing the **exact** event-specific session id as an argument — not "most recent", since another session could be active elsewhere. Returns immediately.

### 8.3 `<worker>_runner.*` — the detached child

- WHY: the slow/paid step lives entirely outside the hook's timeout.
- Own timeout constant wrapping the actual external call, with a catch-timeout branch that logs, best-effort notifies the user, and returns a non-zero/error status.
- Notification (best effort, never changes the exit code, silent no-op off macOS / without `osascript` / when `state.json.digest_notification` is false): the runner snapshots before the LLM call (taken inbox files: count + bytes; hash of every long-memory page except reserved `index.md`/`log.md`; short-memory lines) and, on success (after inbox deletion + metrics), diffs deterministically (never parsing LLM output). Title `🧠 brain · digest ✓`, subtitle `📥 N sessions · size`, body `📚 ✚ created ✎ updated ✖ deleted` (long lists end in `+N more`; `📚 no changes` when none) plus `⚡ +N: <first added short-memory line>` only if short-memory gained lines. Failure/timeout use `✗` with body `exit code N · inbox kept` / `timed out after Ns · inbox kept`. Test hooks: `BRAIN_NOTIFY_CMD` (replaces `osascript`), `BRAIN_NOTIFY_PLATFORM` (replaces the platform check).
- If it must write progress somewhere the user can see, consider that the host's own UI may still be tearing down the display immediately after the child starts — a short delay before the first write can avoid output being clobbered. Any user-facing progress mirroring should be best-effort and silently no-op if there's nowhere to write it.
- Runs the capture step first (raw session material → the store's inbox), then the actual summarization/LLM step, reading its own configured model/parameters from `state.json` each run (not hardcoded) — lets a config subcommand change behavior without touching code. Logs every step and exit code to the shared log file regardless of success or failure.

### 8.4 `<capture>.*` — pulls raw material into the store

- WHY: both the automatic worker (8.3) and a manual backfill command need the exact same "turn raw material into a store-formatted note" logic — one script, two call modes.
- Two invocation modes: one exact session (used by the detached worker) and "last N" (used by a manual backfill subcommand). Reject anything else with a usage message, non-zero exit.
- Reads directly from wherever the host persists session history, if it persists it in an inspectable form — read-only, never mutating that store.
- Renders one output file per captured unit with full frontmatter, written atomically (write to a temp path, then rename/replace).
- Prints a tabular report (id, date, count, size, filename) so the skill can show it verbatim; prints a one-line summary last.

### 8.5 `<plugin>_cli.*` — everything else the skill dispatches to a script

- WHY: one file for every deterministic on/off/status/config/health-check subcommand, kept separate from hook logic and from capture logic.
- `on` / `off`: flip `state.json.enabled`, print a one-line confirmation.
- `status`: print enabled flag + every config value + size/count of every store subfolder.
- `<config-key> [value]`: no-arg form prints current value, one-arg form sets and persists it.
- `doctor`: a **deterministic, enumerable** pass/fail checklist — skill definition exists, every required file under it exists, hooks are wired to the right scripts, every store file/folder that must exist does, every config key that must be set is set. It is also the corruption check after a bad digest: every long-memory page has valid OKF frontmatter, `index.md` lists exactly the pages that exist, cross-links resolve, and `short-memory.md` is within its caps (≤100 lines, each ≤200 chars). Print one pass/fail line per check, then a final summary line, exit accordingly. This is the single place that encodes "what a correctly installed instance of this plugin looks like" — keep it exhaustive and update it whenever the layout changes.
- A flat dispatch matching exact argument shapes; anything unmatched prints a usage string and exits with an error code.

---

## 9. Reference/cookbook files — procedures the *agent* executes, not scripts [generic pattern]

Some steps are judgment calls (what counts as a "durable fact", where it belongs, when to compress vs. append) that are better specified as a numbered procedure for the agent to follow with its own file tools (read, search, write) than hardcoded in a script. Template for such a cookbook:

1. Enumerate the exact precondition check and the exact "nothing to do" message/stop condition.
2. Enumerate the exact classification rule (what kind of material goes to which destination file/folder).
3. State the **merge-not-append** rule explicitly and give a concrete failure mode to avoid ("a digest that only grows a file is a failed digest").
4. If there's a hard size cap on an injected file, state the exact number and the exact eviction policy (oldest/least-relevant first, compress in place before deleting).
5. Require updating any index/log/manifest file as part of the same pass; specify header format exactly if time-sensitive (e.g. include time of day, not just date, if multiple runs can happen the same day).
6. Require preserving the structured-format frontmatter on every file touched, and signing with a consistent "generated by" marker (so it's possible to tell which writer — script vs. agent — produced which edit).
7. Require deleting/cleaning up the raw material once folded in — raw captures are read-then-deleted, never edited in place.
8. End with a mandatory short summary the agent reports back (what was processed, what was touched, what if anything got promoted to the injected/short-term file).

This same cookbook must be invokable **both** inline (the agent follows it itself, live, during an interactive skill command) **and** headlessly (the detached worker passes its literal file contents as a prompt to a fresh agent invocation, if the host supports a non-interactive "run a prompt" mode) — write it so both read the identical file, never fork the logic into two copies.

---

## 10. Structured-knowledge-format rules (OKF v0.1)

- Every long-memory page (except the skill/command definition itself) is an OKF v0.1 concept: YAML frontmatter per the OKF spec, plus `tags: [...]`, `status`, `generated: {by, at}` as extension fields where OKF allows them.
- Pages link to each other with standard markdown links per OKF's cross-linking rules; reserved OKF filenames are respected.
- An index file is a restricted listing page: one line per page it indexes.
- A log file is append-only-at-the-top (newest-first), one dated section per run.
- Inbox/raw-capture files are tagged as drafts/notes, distinct from curated long-term pages.
- The injected short-term file deliberately has **no** frontmatter (every byte counts against its caps: 100 lines, max 200 chars per line) and is the one file that's a flat body, not a structured page.

---

## 11. Illustrative command surface [generic pattern — illustrative values]

| Command | Underlying call | Notes |
| --- | --- | --- |
| `/<plugin> on` / `off` | `<plugin>_cli on\|off` | flips `state.json.enabled` |
| `/<plugin> status` | `<plugin>_cli status` | enabled flag, inbox/short/long sizes |
| `/<plugin> init N` | `<capture> N` then offer `/<plugin> digest` | backfill N past sessions into inbox |
| `/<plugin> digest` | inline, agent follows the cookbook itself | manual digest, using whatever model/agent is currently active |
| `/<plugin> digest model [id]` | `<plugin>_cli digest model [id]` | get/set the **automatic background** digest's model only |
| `/<plugin> digest notification [on\|off]` | `<plugin>_cli digest notification [on\|off]` | get/set the digest-finished notification (`state.json.digest_notification`, default on) |
| `/<plugin> doctor` | `<plugin>_cli doctor` | deterministic install/health check |

---

## 12. How to instantiate this pattern for a new plugin [generic pattern]

1. Pick the plugin's one job (here: cross-session memory). Confirm it needs exactly the three primitives in §1 — if it needs something none of the target hosts support (e.g. a true background process outliving the session, or persistent native memory), this pattern is the wrong fit, or needs a host-specific workaround.
2. Name the plugin, create `skills/<plugin>/{<skill-definition>, config/state.json, references/, scripts/}` and the hook wiring per §3/§5/§6/§7, in whatever format the target host actually requires.
3. Decide what, if anything, must survive in the agent's context on every single session (→ a short, hard-capped, no-frontmatter file injected at session start) vs. what's fine to leave on-disk for on-demand search (→ the rest of the store).
4. Decide whether anything needs to happen automatically when a session ends. If yes and it's slow/paid: build the fast-hook + detached-worker split (§8.2/§8.3) from day one — retrofitting it later after discovering a shutdown-budget kill is expensive.
5. Write `install.sh` per §4 exactly — copy the structure and rename paths; don't redesign it, the idempotent/non-destructive contract is the part most worth reusing verbatim.
6. Write `doctor` (§8.5) as you build each piece, not at the end — it is the executable definition of "correctly installed" and catches drift immediately.
7. Keep a `docs/plan.md`-equivalent as the one authoritative design doc: goal, constraints, full layout, and a running "Implementation — as built" log of what actually shipped vs. the original plan.
8. Write a contributor guide mirroring §2: hard constraints section, script header convention, "not live until reinstalled" reminder, no empty stubs, no commits without explicit ask.

---

## 13. Per-agent adaptation notes [where the abstraction meets a real host]

Every concrete agent/host differs in at least these dimensions — resolve each one against that host's own documentation before implementing:

- **Skill/command format & discovery.** Some hosts use a markdown file with frontmatter dispatched by slash command; others use a manifest + registered tool; others have no command surface at all and only expose hooks (in which case the "skill" becomes a thin CLI the user runs directly, or the hooks alone cover the whole feature).
- **Hook mechanism & payload shape.** JSON config file vs. code callback vs. plugin API; what identifying/contextual fields each event payload actually carries (don't assume a session id is always present or always stable across resume-style flows).
- **Context injection mechanism.** Whether/how a hook can contribute text into the agent's next turn at all — some hosts may only support this at session start, or not support it programmatically, in which case the "inject short-term memory" step becomes "tell the user to paste it" or "rely on the agent reading a well-known file path by its own initiative via an instructions file".
- **Non-interactive invocation.** Whether the host offers a "run a single prompt headlessly" mode the detached worker can call for the summarization step; if not, the digest step may need to run inline only (interactive), with no automatic background digest.
- **Shutdown budget.** Confirm empirically whether the host enforces an aggressive session-end timeout and what it actually is — don't assume §2's detach pattern is necessary until you've seen a hook get killed.
- **Session history persistence.** Whether the host stores session/turn history anywhere inspectable (file, local database) that a capture script can read — if not, capture can only work for the live-ending session's own payload, not backfill of past sessions.
- **Store location.** `~/.agents/brain/` is a suggested, host-agnostic default so the same store could in principle be shared across multiple agents on one machine; a given host's conventions may dictate a different root (e.g. a host-specific config directory) — keep the store itself independent of any single host's directory even if the installed skill/hook wiring has to live inside that host's own directory.

---

## 14. Ready-to-copy templates [generic pattern — adapt placeholders per host]

Four files that change little between hosts — the config schema and the *content/behavior* of instructions, digest cookbook, and skill dispatch are host-independent; only the skill-file *frontmatter* (§6) and the literal script invocation paths need adjusting per target agent. Copy these verbatim into a new plugin and fill in `<plugin>` / `<host-specific path>` placeholders.

### 14.1 `config/state.json`

```json
{
  "enabled": true,
  "digest_model": "auto",
  "digest_notification": true
}
```

(`"digest_notification": true|false` defaults to `true` when missing and is toggled by `<plugin>_cli digest notification on|off`.)

- `enabled` — master on/off switch every hook and subcommand checks first.
- `digest_model` — which model the *automatic background* digest worker uses; `"auto"` defers to the host's own default-model selection. Kept separate from whatever model happens to be active in an interactive session, since the background worker runs headlessly with no active session to inherit a model from.

### 14.2 `references/instructions.md`

Injected verbatim at session start, concatenated with the short-term memory file. No frontmatter — every byte counts toward what gets injected on every single session.

```markdown
# Brain

You have a persistent memory layer under `~/.agents/brain/`. Files are
plain markdown, loaded on demand - you decide when to read more.

## Where things live

- `short-memory.md` - already injected above. Durable, high-level facts.
- `long-memory/index.md` - map of every long-memory page. Read it first
  when you need something not in short-memory.
- `long-memory/log.md` - dated history of what the digest recorded.
- `long-memory/projects/<name>/` - per-project pages (entities, stories,
  tasks, glossary), created only when there's material for them.
- `long-memory/team/`, `long-memory/personal/`, `long-memory/misc/` - team,
  personal, and everything-else facts.
- `inbox/` - raw session dumps waiting to be folded in; not for you to read.

## How to use it

- Search with `grep -r` over `~/.agents/brain/long-memory/` - don't assume
  a page exists, check `index.md` or grep first.
- Don't write directly into `short-memory.md` or `long-memory/` on your own
  initiative; that's the digest's job. If something durable comes up
  mid-session, just say it naturally - it will be captured and folded in
  when the session ends.
- **Exception:** if the user explicitly asks you to update/write/fix memory
  now (not just states a fact in passing), edit the relevant file(s)
  directly, right away - following the same rules the digest follows
  (merge, don't append; keep frontmatter; update `index.md`/`log.md` if a
  page changed).
- If the brain feels stale or wrong, tell the user; don't silently patch it.
```

### 14.3 `references/digest.md`

The digest cookbook — followed both inline (interactive `/<plugin> digest`) and headlessly (the detached worker passes this file's contents as a prompt). Never fork this into two copies; both paths read the same file.

```markdown
# Digest cookbook

Fold everything in `~/.agents/brain/inbox/` into long-term (and, if VERY
important, short-term) memory. No script - do this yourself with your own
file tools (`grep`, read, write).

1. List `~/.agents/brain/inbox/`. If empty, report "inbox empty, nothing to
   digest" and stop.
2. Read every file in `inbox/`. For each durable fact (people, decisions +
   rationale, project state, preferences, commitments, pitfalls - not
   trivia, not code, not raw transcript):
   - Project material → `long-memory/projects/<name>/` on the matching page
     (`entities.md`, `stories.md`, `tasks.md`, `glossary.md` - create only
     what the material needs, never empty stubs).
   - Team / personal / other durable facts → `long-memory/team/`,
     `long-memory/personal/`, `long-memory/misc/` respectively.
   - **VERY IMPORTANT facts** (things that must be in every session's
     context, not just discoverable on demand - e.g. an active hard
     constraint, a standing preference that changes your behavior every
     time) → also fold into `~/.agents/brain/short-memory.md`.
3. **Merge, never append.** A new fact replaces or edits a stale line - a
   digest that only grows a file is a failed digest. Never invent facts;
   never rewrite the user's stated opinions in your own voice. Use
   declarative phrasing (`Prefers X`, not `Always do X`).
4. `short-memory.md` has a **hard limit of 100 lines, each at most 200
   characters** - never exceed either.
   Before/while adding anything new, count lines; if adding would push the
   file over 100, first evict or compress: drop the oldest, least-relevant
   facts (superseded, stale, or lowest-impact-if-forgotten first - not
   necessarily physically last in the file) until there's room, compressing
   in place where possible instead of deleting outright. End the digest
   with the file at ≤100 lines of ≤200 chars, never more. Promotion and
   eviction are your judgment.
5. Update `long-memory/index.md` (one line per page listing) and prepend a
   dated entry to `long-memory/log.md` (newest-first) describing what this
   digest changed. Header must be `## YYYY-MM-DD HH:MM` (include the time -
   there can be several digests per day).
6. Every page keeps its frontmatter; sign writes `generated.by: agent:<host>`.
7. **Delete every digested file from `inbox/`** - it must end empty. Never
   edit inbox files, only read then delete.
8. Report a short summary: how many inbox files processed, which
   long-memory pages touched, whether anything was promoted to
   short-memory.
```

### 14.4 Skill/command definition (`SKILL.md` or host equivalent)

Frontmatter follows whatever format the target host requires (§6) — the block below is illustrative, modeled on a frontmatter-plus-markdown-body skill format; replace the YAML keys with your host's actual schema:

```markdown
---
name: <plugin>
description: Persistent second-brain memory layer across sessions. Use when
  the user runs /<plugin>, or asks to turn memory on/off, check status,
  extract past sessions, or digest inbox notes into long-term memory.
license: <your license>
compatibility: <target host>
argument-hint: "[on|off|status|init|digest|digest model|digest model <model-id>|digest notification [on|off]|doctor]"
metadata:
  author: <you>
  version: 0.0.1
---

# Brain

A minimal on/off switch, status readout, session-extraction, and digest
trigger for the second-brain memory layer.

## How It Works

Find the literal substring `/<plugin>` in the invoking message, take
everything after it, and trim leading/trailing whitespace. Call this the
**command text**.

Then look at the first word of the command text and dispatch - do not
guess, do not default to `status` if unsure; if the first word isn't one
of these, show the Commands table below and stop:

| First word of command text | Action |
| --- | --- |
| `on` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* on`, show output verbatim. |
| `off` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* off`, show output verbatim. |
| `status` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* status`, show output verbatim. |
| `init` (`init N`) | Run `<script-runner> <skill-dir>/scripts/<capture>.* N` (N = the number after `init`), show its report table verbatim (id, date, turns, size, file), then ask the user w[…] |
| `digest` alone | **Do not run a script.** Follow `references/digest.md` yourself, inline, using your own file tools (`grep`, read, write) - same cookbook the automatic digest runs, but wit[…] |
| `digest model` (no id) | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* digest model`, show output verbatim. |
| `digest model <model-id>` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* digest model <model-id>`, show output verbatim. |
| `digest notification` / `digest notification on` / `digest notification off` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* digest notification [on\|off]`, show output verbatim. |
| `doctor` | Run `<script-runner> <skill-dir>/scripts/<plugin>_cli.* doctor`, show output verbatim. |

## Commands

| Command | Purpose |
| ------- | ------- |
| `/<plugin> on` | Sets `state.json.enabled = true` |
| `/<plugin> off` | Sets `state.json.enabled = false` - hooks become no-ops; hooks and subcommands skip to run |
| `/<plugin> status` | Shows enabled state, inbox files and sizes, short memory size, long memory size |
| `/<plugin> init N` | Extracts the last N sessions into `inbox/`, prints the report, then offers to run `/<plugin> digest` |
| `/<plugin> digest` | Manually runs the digest cookbook above, inline, right now |
| `/<plugin> digest model` | Prints the current `state.json.digest_model` |
| `/<plugin> digest model [model-id]` | Sets `state.json.digest_model = model-id` - model the automatic background digest will use |
| `/<plugin> digest notification [on\|off]` | Prints / sets `state.json.digest_notification` (digest-finished desktop notification, default on) |
| `/<plugin> doctor` | Deterministic health check: skill install, hooks wiring, long-memory structure, short-memory file, hook scripts, instructions.md, state.json, digest_model - pass/fail[…] |
```

---

## 15. Dashboard (`/<plugin> dashboard`) — POST-MVP

> **Out of scope for the MVP.** Captured here as the target design for a later version; the MVP command surface (§11, §14.4) does not include it. Tech stack also out of scope.

A local, single-user view of the brain for inspecting and hand-editing memory. The tech stack is deliberately not chosen here; whatever is picked must still respect §2 (global store only, no external service, no API keys).

### 15.1 Launch

- `/<plugin> dashboard` → `<plugin>_cli dashboard` starts (or reuses) the dashboard and prints its local URL; the skill shows it verbatim.
- Local only: bound to the loopback interface, never exposed on the network.
- Reads and writes only inside the store (`~/.agents/brain/`); never touches a project repo.
- If `state.json.enabled` is `false`, the dashboard still opens (read/edit is useful while hooks are off) and shows the disabled state prominently.

### 15.2 Overview screen

Three widgets:

| Widget | Shows | On click |
| --- | --- | --- |
| **Short memory** | Current size: line count vs. the 100-line cap, plus longest-line width vs. the max-width cap | Opens the short-memory editor (15.3) |
| **Long memory** | Page count, total size, tag count | Opens the long-memory graph (15.4) |
| **Growth** | Graph over time of short-memory lines, long-memory page count and total size, one point per digest | — |

Growth data source: each digest run records one snapshot (timestamp, short-memory lines, long-memory pages, long-memory bytes, tag count) to an append-only metrics file in the store (e.g. `~/.agents/brain/metrics.jsonl`). Like `<plugin>.log`, this is a log, so append is correct here; §2's merge-not-append rule applies to content pages, not logs. `/<plugin> init` / backfill may seed an initial point.

### 15.3 Short-memory editor

- Loads `short-memory.md` into an editable text area.
- Live counters for line count and longest line against the caps; save is **blocked** while either cap is exceeded.
- No frontmatter is added (§10: the short-term file stays a flat body).

### 15.4 Long-memory graph (Obsidian-style)

- Force-directed graph of `long-memory/`:
  - **Tag nodes** — one per distinct tag across all pages' frontmatter, labeled with the tag.
  - **Leaf nodes** — one per page, labeled with its file name.
  - **Edges** — page → each of its tags; plus page → page for markdown cross-links (OKF links), drawn distinctly.
- Pan, zoom, drag; hovering a node highlights its neighbors.
- Clicking a **leaf** opens a right-hand side panel with that page's content in an editor (frontmatter + body).

### 15.5 Edit rules (both editors)

Edits from the dashboard follow the same rules as the digest (§9, §14.3), because they write the same files:

- Atomic write (temp file, then rename).
- Conflict check: if the file changed on disk since it was loaded (e.g. a digest ran), refuse the save and offer to reload; never silently overwrite.
- Long-memory pages must keep valid OKF frontmatter; save is blocked if it doesn't parse. Edits are signed `generated.by: human:dashboard`.
- Any long-memory save that adds, removes or renames a page also updates `long-memory/index.md` and prepends an entry to `long-memory/log.md` (`## YYYY-MM-DD HH:MM`).
- Every save is logged to `<plugin>.log`.

### 15.6 `doctor` additions

- Metrics file exists and every line parses.
- Dashboard entry point is installed and executable.

---

## 16. Accepted limitations (MVP)

- **No secret redaction or client isolation.** Raw transcripts land in one global store; tokens and client facts are not stripped or partitioned. Known compliance risk on client work; revisit before using on client data.
- **No lock.** Assumes one interactive session at a time with the digest running on quit. Two digests at once would race (last write wins).
- **No rollback.** Inbox captures are deleted after digest. `doctor` (§8.5) detects structural corruption but cannot restore facts lost by a bad merge.
- **Promotion is model judgment.** A fact may be promoted and evicted on alternating digests.
- **No dashboard.** `/<plugin> dashboard` (§15) is post-MVP.
- **Single user.** The brain is per-user; multi-agent orchestration is out of scope.

---

## 17. Evaluation

Two complementary test layers, both runnable headlessly and repeatably:

### 17.1 Digest fixtures (correctness)

- Canned inbox transcripts in, a known starting store, then run the digest cookbook headlessly.
- Assert: expected facts land on the expected long-memory pages; every page is valid OKF; `index.md` and `log.md` are updated; `short-memory.md` stays within its caps (≤100 lines, ≤200 chars each); `inbox/` ends empty; no fact appears that is absent from the fixtures (no invention).
- Zero-turn session fixture: `session_end` must not spawn a digest.
- Recursion fixture: with the sentinel env var set, both hooks emit nothing.

### 17.2 Recall probes (value)

- A fixed set of questions whose answers live only in the brain (e.g. "what stack do I prefer?", "what did we decide about X?").
- Run each probe headlessly twice: brain on and brain off (`/<plugin> off`). Score the answers against expected facts.
- Track the on/off score over time; the brain earns its keep only while "on" clearly beats "off".

<!-- Transcribed from 12 photos. "[…]" marks text cut off at the right edge of the screen. The photos end here; the source file may continue past §14.4. -->
