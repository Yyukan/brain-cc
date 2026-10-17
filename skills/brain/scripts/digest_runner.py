#!/usr/bin/env python3
# WHY:  the digest is a slow, paid LLM call; it must live outside the SessionEnd hook's
#       timeout budget, in its own detached process with its own timeout.
# WHAT: `digest_runner.py <session_id> <transcript_path>`: capture that exact session into
#       the inbox, run a headless `claude -p` with references/digest.md as the prompt
#       (cwd=store, scoped tools, BRAIN_HEADLESS=1, model from state.json), and on success
#       record a metrics snapshot. Logs every step and exit code. Ends with a best-effort macOS
#       notification (✓ with a deterministic before/after diff of long-memory pages and
#       short-memory lines, ✗ on failure/timeout); never affects the exit code. Off via
#       state.json digest_notification=false. Env (tests): BRAIN_NOTIFY_CMD, BRAIN_NOTIFY_PLATFORM.
# WHO:  spawned detached by session_end.py; may also be run by hand for debugging.
import hashlib
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


NOTIFY_CMD_ENV = "BRAIN_NOTIFY_CMD"
NOTIFY_PLATFORM_ENV = "BRAIN_NOTIFY_PLATFORM"
MAX_NAMES = 4        # page names listed per group before "+N more"
MAX_NAME_LEN = 40
MAX_BODY = 230
SHORT_PREVIEW = 70


def human_bytes(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024)
    return "%.1f MB" % (n / (1024 * 1024))


def clean(text):
    """One line, no control characters."""
    return " ".join("".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in str(text)).split())


def as_applescript(text):
    """Quoted AppleScript string literal: control chars flattened, backslashes then quotes escaped."""
    return '"%s"' % clean(text).replace("\\", "\\\\").replace('"', '\\"')


def build_script(title, subtitle, body):
    return "display notification %s with title %s subtitle %s" % (
        as_applescript(body), as_applescript(title), as_applescript(subtitle))


def notify(title, subtitle, body):
    """Best effort; silent no-op off macOS, without osascript, or on any failure."""
    try:
        if os.environ.get(NOTIFY_PLATFORM_ENV, sys.platform) != "darwin":
            return
        cmd = os.environ.get(NOTIFY_CMD_ENV) or "osascript"
        subprocess.run([cmd, "-e", build_script(title, subtitle, body)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


def long_pages_hash(store):
    """{bundle-relative path: sha1} for every *.md under long-memory except reserved index/log."""
    root = store / "long-memory"
    out = {}
    for dp, _, fns in os.walk(str(root)):
        for fn in fns:
            if not fn.endswith(".md") or fn in lib.RESERVED:
                continue
            full = os.path.join(dp, fn)
            if os.path.islink(full):
                continue
            with open(full, "rb") as fh:
                out[os.path.relpath(full, str(root)).replace(os.sep, "/")] = hashlib.sha1(fh.read()).hexdigest()
    return out


def short_lines(store):
    try:
        return (store / "short-memory.md").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def take_snapshot(store, inbox_files):
    """Pre-digest state for the notification, or None if it cannot be taken."""
    try:
        size = 0
        for p in inbox_files:
            try:
                size += p.stat().st_size
            except OSError:
                pass
        return {"count": len(inbox_files), "bytes": size,
                "pages": long_pages_hash(store), "short": short_lines(store)}
    except Exception as exc:
        lib.log(NAME, "notify snapshot failed: %r" % exc)
        return None


def subtitle_for(snap):
    n = snap["count"]
    return "📥 %d session%s · %s" % (n, "" if n == 1 else "s", human_bytes(snap["bytes"]))


def group(sym, names):
    shown = [clean(n[:-3] if n.endswith(".md") else n)[:MAX_NAME_LEN] for n in names[:MAX_NAMES]]
    more = len(names) - len(shown)
    return "%s %s%s" % (sym, ", ".join(shown), (" +%d more" % more) if more > 0 else "")


def success_body(store, snap):
    before, after = snap["pages"], long_pages_hash(store)
    created = sorted(k for k in after if k not in before)
    updated = sorted(k for k in after if k in before and after[k] != before[k])
    deleted = sorted(k for k in before if k not in after)
    parts = [group(s, n) for s, n in (("✚", created), ("✎", updated), ("✖", deleted)) if n]
    out = ["📚 " + " ".join(parts) if parts else "📚 no changes"]
    seen = set(snap["short"])
    added = [ln for ln in short_lines(store) if ln not in seen]
    if added:
        first = clean(added[0])
        if len(first) > SHORT_PREVIEW:
            first = first[:SHORT_PREVIEW - 1] + "…"
        out.append("⚡ +%d: %s" % (len(added), first))
    body = " · ".join(out)
    return body if len(body) <= MAX_BODY else body[:MAX_BODY - 1] + "…"


def notify_result(state, snap, status, store=None, detail=""):
    """status 'ok' | 'fail'. Never raises."""
    try:
        if snap is None:
            return
        cur = lib.read_state_raw() or state  # fresh read: a toggle during the run counts
        if cur.get("digest_notification", True) is False:
            return
        if status == "ok":
            title, body = "🧠 brain · digest ✓", success_body(store, snap)
        else:
            title, body = "🧠 brain · digest ✗", detail
        notify(title, subtitle_for(snap), body)
    except Exception as exc:
        lib.log(NAME, "notify failed: %r" % exc)


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
    snap = take_snapshot(store, snapshot)
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
        notify_result(state, snap, "fail", detail="timed out after %ds · inbox kept" % int(timeout))
        return 124
    lib.log(NAME, "claude exit code %s; output tail: %s" % (rc, tail))
    if rc != 0:
        notify_result(state, snap, "fail", detail="exit code %s · inbox kept" % rc)
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
    notify_result(state, snap, "ok", store=store)
    return 0


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception as exc:
        lib.log(NAME, "error: %r" % exc)
        rc = 1
    sys.exit(rc)
