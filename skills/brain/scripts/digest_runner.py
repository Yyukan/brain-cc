#!/usr/bin/env python3
# WHY:  the digest is a slow, paid LLM call; it must live outside the SessionEnd hook's
#       timeout budget, in its own detached process with its own timeout.
# WHAT: `digest_runner.py <session_id> <transcript_path>`: capture that exact session into
#       the inbox, run a headless `claude -p` with references/digest.md as the prompt
#       (cwd=store, scoped tools, BRAIN_HEADLESS=1, model from state.json), and on success
#       record a metrics snapshot. Logs every step and exit code; on timeout logs, notifies
#       (best effort) and exits non-zero.
# WHO:  spawned detached by session_end.py; may also be run by hand for debugging.
import os
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_lib as lib  # noqa: E402
import capture  # noqa: E402

NAME = "digest_runner"
DIGEST_TIMEOUT = 900  # seconds; BRAIN_DIGEST_TIMEOUT overrides (tests)
ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash(ls:*),Bash(date:*)"
HERE = os.path.dirname(os.path.abspath(__file__))


def notify(msg):
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            'display notification "%s" with title "brain"' % msg.replace('"', "'")],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


def timeout_seconds():
    try:
        return max(1.0, float(os.environ.get("BRAIN_DIGEST_TIMEOUT", DIGEST_TIMEOUT)))
    except ValueError:
        return float(DIGEST_TIMEOUT)


def run_claude(cmd, store, env, timeout):
    proc = subprocess.Popen(cmd, cwd=store, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, text=True, errors="replace")
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, _ = proc.communicate()
        return None, out or "", True


def main(argv):
    if len(argv) != 2:
        print("usage: digest_runner.py <session_id> <transcript_path>", file=sys.stderr)
        return 2
    sid, tpath = argv
    store = lib.store_dir()
    lib.log(NAME, "start session=%s transcript=%s" % (sid, tpath))
    state = lib.load_state()
    if not state or state["enabled"] is not True:
        lib.log(NAME, "abort: brain disabled or state invalid")
        return 1
    row, err = capture.capture_session(sid, tpath)
    if err:
        lib.log(NAME, "capture failed: " + err)
        return 1
    lib.log(NAME, "capture ok: %s" % row["file"])
    cookbook = lib.skill_dir() / "references" / "digest.md"
    try:
        digest = cookbook.read_text(encoding="utf-8")
    except OSError as exc:
        lib.log(NAME, "abort: cannot read %s: %s" % (cookbook, exc))
        return 1
    claude = lib.find_claude()
    if not claude:
        lib.log(NAME, "abort: claude binary not found (set %s)" % lib.CLAUDE_BIN_ENV)
        return 1
    inbox = (store / "inbox").resolve()
    snapshot = sorted(p for p in inbox.glob("*.md") if p.is_file() and not p.is_symlink())
    lib.log(NAME, "inbox snapshot: %d file(s)" % len(snapshot))
    prompt = ("Brain store root: %s (use it wherever the cookbook says ~/.agents/brain/). "
              "Headless mode: do not delete inbox files yourself; the runner removes them after "
              "you finish (skip the inbox-deletion step of the cookbook).\n\n%s" % (store, digest))
    cmd = [claude, "-p", prompt]
    model = state["digest_model"]
    if model != "auto":
        cmd += ["--model", model]
    cmd += ["--add-dir", str(store), "--allowedTools", ALLOWED_TOOLS]
    env = dict(os.environ)
    env[lib.HEADLESS_ENV] = "1"
    timeout = timeout_seconds()
    lib.log(NAME, "running headless digest (model=%s timeout=%ss)" % (model, int(timeout)))
    try:
        rc, out, timed_out = run_claude(cmd, str(store), env, timeout)
    except OSError as exc:
        lib.log(NAME, "claude failed to start: %s" % exc)
        return 1
    tail = (out or "")[-1500:]
    if timed_out:
        lib.log(NAME, "TIMEOUT after %ss; output tail: %s" % (int(timeout), tail))
        notify("digest timed out for session %s" % sid[:8])
        return 124
    lib.log(NAME, "claude exit code %s; output tail: %s" % (rc, tail))
    if rc != 0:
        return rc or 1
    for p in snapshot:
        try:
            rp = p.resolve()
            if rp.parent != inbox:
                lib.log(NAME, "refusing to delete %s (outside inbox)" % p)
                continue
            rp.unlink()
            lib.log(NAME, "deleted digested inbox file %s" % rp.name)
        except FileNotFoundError:
            lib.log(NAME, "inbox file already gone: %s" % p.name)
        except OSError as exc:
            lib.log(NAME, "could not delete %s: %s" % (p.name, exc))
    m = subprocess.run([sys.executable, os.path.join(HERE, "brain_cli.py"), "metrics"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env)
    lib.log(NAME, "metrics exit code %s" % m.returncode)
    return 0


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception as exc:
        lib.log(NAME, "error: %r" % exc)
        rc = 1
    sys.exit(rc)
