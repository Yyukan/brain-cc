#!/usr/bin/env python3
# WHY:  the real `claude -p` is slow, paid and non-deterministic; tests need a stand-in that
#       applies a known digest to the store so the plumbing and the invariants are checkable.
# WHAT: fake `claude` (used via BRAIN_CLAUDE_BIN). Reads the inbox in cwd, applies the canned
#       digest_plan.json (a fact is written only if its needle occurs in the inbox, so nothing
#       is invented): writes OKF pages, rebuilds index.md, prepends a log.md section, edits
#       short-memory.md within caps. Does NOT delete the inbox (the runner does).
#       Env: BRAIN_FAKE_MARKER=<file> writes a JSON record of the invocation;
#       BRAIN_FAKE_MODE=ok|fail|sleep; BRAIN_FAKE_EXIT=<code> for fail (default 3);
#       BRAIN_FAKE_SLEEP=<seconds> for sleep.
# WHO:  tests/test_*.py via helpers.FAKE_CLAUDE; never shipped.
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def now():
    return datetime.datetime.now().astimezone()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def split_fm(text):
    lines = text.split("\n")
    end = lines.index("---", 1)
    return "\n".join(lines[: end + 1]) + "\n", "\n".join(lines[end + 1:])


def page_meta(path):
    meta = {}
    with open(path, encoding="utf-8") as fh:
        for ln in fh.read().split("\n")[1:]:
            if ln == "---":
                break
            k, _, v = ln.partition(": ")
            meta[k] = v
    return meta


def main(argv):
    marker = os.environ.get("BRAIN_FAKE_MARKER")
    store = os.getcwd()
    prompt = argv[2] if len(argv) > 2 and argv[1] == "-p" else ""
    m = re.match(r"Brain store root: (.+?) \(use it", prompt)
    inbox_dir = os.path.join(store, "inbox")
    inbox_files = sorted(f for f in os.listdir(inbox_dir) if f.endswith(".md")) if os.path.isdir(inbox_dir) else []
    if marker:
        write(marker, json.dumps({
            "argv": argv[1:], "cwd": os.path.realpath(store),
            "store_root": m.group(1) if m else None,
            "headless": os.environ.get("BRAIN_HEADLESS"), "inbox": inbox_files}))
    mode = os.environ.get("BRAIN_FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(float(os.environ.get("BRAIN_FAKE_SLEEP", "30")))
        return 0
    if mode == "fail":
        print("fake claude: simulated failure", file=sys.stderr)
        return int(os.environ.get("BRAIN_FAKE_EXIT", "3"))

    text = ""
    for f in inbox_files:
        with open(os.path.join(inbox_dir, f), encoding="utf-8") as fh:
            text += fh.read() + "\n"
    if not inbox_files:
        print("inbox empty, nothing to digest")
        return 0
    with open(os.path.join(HERE, "digest_plan.json"), encoding="utf-8") as fh:
        plan = json.load(fh)
    stamp = now()
    iso = stamp.isoformat(timespec="seconds")
    long_root = os.path.join(store, "long-memory")
    facts = [f for f in plan["facts"] if f["needle"] in text]
    written = {f["page"] for f in facts}
    touched = []
    for f in facts:
        path = os.path.join(long_root, f["page"])
        bullets = []
        if os.path.isfile(path):
            _, body = split_fm(open(path, encoding="utf-8").read())
            bullets = [ln for ln in body.split("\n") if ln.startswith("* ")]
        for ln in ["* " + f["line"]]:
            if ln not in bullets:
                bullets.append(ln)
        links = ["* See [%s](%s)" % (l, l) for l in f.get("links", [])
                 if os.path.join(os.path.dirname(f["page"]), l) in written]
        bullets = [b for b in bullets if not b.startswith("* See [")] + links
        fm = ["---", "type: %s" % f["type"], "title: %s" % f["title"],
              "description: %s" % f["description"], "tags: [%s]" % ", ".join(f["tags"]),
              "status: stable", "generated: { by: agent:claude-code, at: %s }" % iso, "---"]
        write(path, "\n".join(fm) + "\n\n# %s\n\n%s\n" % (f["title"], "\n".join(bullets)))
        touched.append(f["page"])
    if not facts:
        print("nothing durable to record")
        return 0
    # index: exactly every non-reserved page
    pages = []
    for dp, dn, fn in os.walk(long_root):
        for n in fn:
            rel = os.path.relpath(os.path.join(dp, n), long_root).replace(os.sep, "/")
            if n.endswith(".md") and rel not in ("index.md", "log.md"):
                pages.append(rel)
    idx_path = os.path.join(long_root, "index.md")
    fm, _ = split_fm(open(idx_path, encoding="utf-8").read())
    lines = []
    for rel in sorted(pages):
        meta = page_meta(os.path.join(long_root, rel))
        lines.append("* [%s](/%s) - %s" % (meta["title"], rel, meta["description"]))
    write(idx_path, fm + "\n" + "\n".join(lines) + "\n")
    # short memory: add promoted facts, evict oldest beyond 100 lines, clip to 200 chars
    sm_path = os.path.join(store, "short-memory.md")
    sm = open(sm_path, encoding="utf-8").read().splitlines() if os.path.isfile(sm_path) else []
    promoted = 0
    for s in plan["short"]:
        if s["needle"] in text and s["line"] not in sm:
            sm.append(s["line"])
            promoted += 1
    sm = [ln[:200] for ln in sm][-100:]
    write(sm_path, "\n".join(sm) + ("\n" if sm else ""))
    # log: newest-first, directly below the frontmatter
    log_path = os.path.join(long_root, "log.md")
    fm, body = split_fm(open(log_path, encoding="utf-8").read())
    section = "## %s\n\n* pages: %s\n* promoted to short-memory: %d\n" % (
        stamp.strftime("%Y-%m-%d %H:%M"), ", ".join(touched), promoted)
    write(log_path, fm + "\n" + section + ("\n" + body.strip("\n") + "\n" if body.strip() else ""))
    print("digested %d inbox file(s); pages: %s" % (len(inbox_files), ", ".join(touched)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
