#!/usr/bin/env python3
# WHY:  both the detached digest worker and the manual `init N` backfill need the same
#       "Claude Code transcript -> inbox note" logic; one script, two call modes.
# WHAT: `capture.py N` writes the last N sessions (>=1 user turn, cwd != store) and
#       `capture.py --session <id> [--transcript <path>]` writes one exact session to
#       STORE/inbox/<date>-<session_id>.md (OKF Note, status draft, atomic, deterministic
#       name). Transcripts are only read. Prints a table (id, date, turns, size, file) and a
#       one-line summary last.
# WHO:  digest_runner.py (exact-session mode), the skill's `init N` row (last-N mode), humans.
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_lib as lib  # noqa: E402

NAME = "capture"
USAGE = "usage: capture.py N | capture.py --session <id> [--transcript <path>]"
MSG_CAP = 8000          # chars kept per message
TOTAL_CAP = 400000      # chars kept per capture


def find_transcript(session_id):
    base = lib.claude_dir() / "projects"
    hits = sorted(base.glob("*/%s.jsonl" % session_id), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _text(content, assistant):
    if isinstance(content, str):
        return content.strip()
    out = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                out.append(b["text"].strip())
            elif b.get("type") == "image" and not assistant:
                out.append("[image]")
    return "\n\n".join(t for t in out if t)


def _clip(t):
    return t if len(t) <= MSG_CAP else t[:MSG_CAP] + "\n[... truncated]"


def _date(ts, fallback):
    if isinstance(ts, str) and ts:
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d")
        except ValueError:
            pass
    return fallback


def read_session(path):
    """Parse a transcript. Returns dict(cwd, date, turns, messages)."""
    fallback = datetime.date.fromtimestamp(os.stat(path).st_mtime).isoformat()
    cwd, first_ts, turns, msgs = "", None, 0, []
    for o in lib.iter_transcript(path):
        if not cwd and isinstance(o.get("cwd"), str):
            cwd = o["cwd"]
        msg = o.get("message") if isinstance(o.get("message"), dict) else None
        if o.get("type") == "user" and lib.is_user_turn(o):
            turns += 1
            if first_ts is None:
                first_ts = o.get("timestamp")
            t = _text(msg.get("content"), False)
            if t:
                msgs.append(("User", t))
        elif o.get("type") == "assistant" and msg:
            t = _text(msg.get("content"), True)
            if t:
                msgs.append(("Assistant", t))
    return {"cwd": cwd, "date": _date(first_ts, fallback), "turns": turns, "messages": msgs}


def render(session_id, info):
    now = lib.now_iso()
    head = [
        "---",
        "type: Note",
        "title: %s" % lib.yaml_quote("Session %s %s" % (info["date"], session_id[:8])),
        "description: %s" % lib.yaml_quote("Raw capture of Claude Code session %s." % session_id),
        "tags: [inbox, session]",
        "status: draft",
        "generated: { by: process:capture, at: %s }" % now,
        "session_id: %s" % lib.yaml_quote(session_id),
        "cwd: %s" % lib.yaml_quote(info["cwd"]),
        "turns: %d" % info["turns"],
        "---",
        "",
        "# Session %s" % session_id,
        "",
    ]
    body, used = [], 0
    for who, text in info["messages"]:
        chunk = "## %s\n\n%s\n" % (who, _clip(text))
        if used + len(chunk) > TOTAL_CAP:
            body.append("[... capture truncated]\n")
            break
        used += len(chunk)
        body.append(chunk)
    return "\n".join(head) + "\n" + "\n".join(body)


def under_store(cwd):
    if not cwd:
        return False
    try:
        c = Path(cwd).resolve()
        s = lib.store_dir().resolve()
    except OSError:
        return False
    return c == s or s in c.parents


def capture_one(session_id, path):
    """Write the inbox note. Returns (row, error)."""
    if not lib.SESSION_ID_RE.match(session_id):
        return None, "invalid session id %r" % session_id
    if not lib.store_dir().is_dir():
        return None, "store %s does not exist (run install.sh)" % lib.store_dir()
    try:
        info = read_session(path)
    except OSError as exc:
        return None, "cannot read transcript %s: %s" % (path, exc)
    if info["turns"] < 1:
        return None, "session %s has no user turns" % session_id
    out = lib.store_dir() / "inbox" / ("%s-%s.md" % (info["date"], session_id))
    try:
        lib.atomic_write(out, render(session_id, info))
    except OSError as exc:
        return None, "cannot write %s: %s" % (out, exc)
    row = {"id": session_id, "date": info["date"], "turns": info["turns"],
           "size": out.stat().st_size, "file": str(out), "cwd": info["cwd"]}
    lib.log(NAME, "captured %s -> %s (%d turns)" % (session_id, out.name, info["turns"]))
    return row, None


def capture_session(session_id, transcript=None):
    path = Path(transcript) if transcript else find_transcript(session_id)
    if not path or not Path(path).is_file():
        return None, "transcript for session %s not found" % session_id
    return capture_one(session_id, path)


def capture_last(n):
    base = lib.claude_dir() / "projects"
    cands = []
    for p in base.glob("*/*.jsonl"):
        try:
            cands.append((p.stat().st_mtime, p))
        except OSError:
            continue
    rows, skipped = [], 0
    for _, p in sorted(cands, key=lambda x: x[0], reverse=True):
        if len(rows) >= n:
            break
        if not lib.SESSION_ID_RE.match(p.stem):
            continue
        try:
            cwd = ""
            for o in lib.iter_transcript(p):
                if isinstance(o.get("cwd"), str):
                    cwd = o["cwd"]
                    break
        except OSError:
            continue
        if under_store(cwd):
            skipped += 1
            continue
        row, err = capture_one(p.stem, p)
        if err:
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


def print_table(rows):
    hdr = ("id", "date", "turns", "size", "file")
    data = [(r["id"], r["date"], str(r["turns"]), str(r["size"]), r["file"]) for r in rows]
    widths = [max(len(hdr[i]), *(len(d[i]) for d in data)) if data else len(hdr[i]) for i in range(5)]
    for line in [hdr] + data:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(line)).rstrip())


def main(argv):
    if len(argv) == 1 and argv[0].isdigit() and int(argv[0]) > 0:
        if not lib.store_dir().is_dir():
            print("error: store %s does not exist (run install.sh)" % lib.store_dir(), file=sys.stderr)
            return 1
        rows, skipped = capture_last(int(argv[0]))
        print_table(rows)
        print("captured %d session(s) into %s (%d skipped)" % (len(rows), lib.store_dir() / "inbox", skipped))
        return 0
    if argv and argv[0] == "--session" and len(argv) in (2, 4):
        transcript = None
        if len(argv) == 4:
            if argv[2] != "--transcript":
                print(USAGE, file=sys.stderr)
                return 2
            transcript = argv[3]
        row, err = capture_session(argv[1], transcript)
        if err:
            lib.log(NAME, "error: " + err)
            print("error: " + err, file=sys.stderr)
            return 1
        print_table([row])
        print("captured 1 session(s) into %s" % (lib.store_dir() / "inbox"))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
