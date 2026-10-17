#!/usr/bin/env python3
# WHY:  the digest is slow and paid; the SessionEnd hook may be killed quickly, so the hook
#       only does cheap checks and hands off to a fully detached worker.
# WHAT: SessionEnd hook. Guards (BRAIN_HEADLESS, enabled, session id recorded by
#       session_start, transcript has >=1 user turn), then spawns digest_runner.py detached
#       with the exact session_id + transcript_path and returns immediately. No stdout.
# WHO:  Claude Code, via the SessionEnd entry merged into settings.json by install.sh.
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_lib as lib  # noqa: E402

NAME = "session_end"
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digest_runner.py")


def main():
    if lib.is_headless():
        return 0
    payload = lib.read_stdin_json()
    if not lib.store_dir().is_dir() or not lib.is_enabled():
        return 0
    sid = payload.get("session_id")
    tpath = payload.get("transcript_path")
    lib.log(NAME, "event reason=%s session=%s" % (payload.get("reason"), sid))
    if not isinstance(sid, str) or not lib.SESSION_ID_RE.match(sid):
        lib.log(NAME, "skip: missing/invalid session_id")
        return 0
    if not lib.session_recorded(sid):
        lib.log(NAME, "skip: session %s not recorded by session_start" % sid)
        return 0
    if not isinstance(tpath, str) or not tpath or not os.path.isfile(tpath):
        lib.log(NAME, "skip: transcript missing for %s" % sid)
        return 0
    tpath = os.path.abspath(tpath)
    if not lib.has_user_turn(tpath):
        lib.log(NAME, "skip: zero user turns in %s" % sid)
        lib.forget_session(sid)
        return 0
    try:
        subprocess.Popen([sys.executable, RUNNER, sid, tpath],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as exc:
        lib.log(NAME, "spawn failed: %s" % exc)
        return 0
    lib.forget_session(sid)
    lib.log(NAME, "spawned digest_runner for %s" % sid)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:  # never break the host
        lib.log(NAME, "error: %r" % exc)
        rc = 0
    sys.exit(rc)
