#!/usr/bin/env python3
# WHY:  inject the brain rules + short-term memory into every new Claude Code session
#       without touching any instructions file the user owns.
# WHAT: SessionStart hook. Guards (BRAIN_HEADLESS, state.enabled, store exists), records the
#       session id for session_end's spurious-event filter, prints the additionalContext JSON.
#       Prints nothing on every no-op path.
# WHO:  Claude Code, via the SessionStart entry merged into settings.json by install.sh.
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_lib as lib  # noqa: E402

NAME = "session_start"


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main():
    if lib.is_headless():
        return 0
    payload = lib.read_stdin_json()
    if not lib.store_dir().is_dir() or not lib.is_enabled():
        return 0
    sid = payload.get("session_id")
    lib.log(NAME, "event source=%s session=%s" % (payload.get("source"), sid))
    if isinstance(sid, str) and lib.SESSION_ID_RE.match(sid):
        try:
            lib.record_session(sid)
        except OSError as exc:
            lib.log(NAME, "could not record session: %s" % exc)
    instructions = read_text(lib.skill_dir() / "references" / "instructions.md").strip()
    short = read_text(lib.store_dir() / "short-memory.md").strip()
    parts = [p for p in (instructions, short) if p]
    if not parts:
        lib.log(NAME, "nothing to inject")
        return 0
    context = lib.inject_store_path("\n\n".join(parts))
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": context}}))
    lib.log(NAME, "injected %d chars" % len(context))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:  # never break the host session
        lib.log(NAME, "error: %r" % exc)
        rc = 0
    sys.exit(rc)
