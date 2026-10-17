#!/usr/bin/env python3
# WHY:  one place for every deterministic, non-hook brain subcommand and for the exhaustive
#       "what a correct install looks like" check.
# WHAT: flat dispatch: on | off | status | digest model [id] | doctor | metrics |
#       dashboard [--no-open] [--out PATH]. Anything else prints usage, exit 2.
# WHO:  the brain skill (SKILL.md dispatch rows), digest_runner.py (metrics), humans.
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_lib as lib  # noqa: E402

NAME = "brain_cli"
HERE = Path(__file__).resolve().parent
USAGE = ("usage: brain_cli.py on | off | status | digest model [<model-id>] | doctor | metrics | "
         "dashboard [--no-open] [--out PATH]")
SCRIPTS = ("brain_lib.py", "session_start.py", "session_end.py", "digest_runner.py",
           "capture.py", "brain_cli.py", "dashboard.py")
SKILL_FILES = ("SKILL.md", ".brain-plugin", "config/state.json", "references/instructions.md",
               "references/digest.md", "assets/dashboard_template.html")
STORE_FILES = ("state.json", "brain.log", ".sessions", "metrics.jsonl", "short-memory.md",
               "long-memory/index.md", "long-memory/log.md")
STORE_DIRS = ("inbox", "long-memory")
LONG_STATUSES = tuple(x for x in lib.STATUSES if x != "draft")
METRIC_KEYS = ("ts", "short_lines", "long_pages", "long_bytes", "tags")
LOG_HDR_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
INDEX_LINE_RE = re.compile(r"^\s*[*-]\s+\[[^\]]*\]\(\s*<?([^)\s>]+)")


def need_store():
    if not lib.store_dir().is_dir():
        print("error: store %s does not exist (run install.sh)" % lib.store_dir(), file=sys.stderr)
        return False
    return True


# ----------------------------------------------------------------- simple commands
def cmd_set_enabled(value):
    if not need_store():
        return 1
    st = lib.read_state_raw() or {}
    st["enabled"] = value
    if not isinstance(st.get("digest_model"), str) or not st["digest_model"].strip():
        st["digest_model"] = "auto"
    lib.write_state(st)
    lib.log(NAME, "enabled=%s" % str(value).lower())
    print("brain %s" % ("enabled" if value else "disabled"))
    return 0


def cmd_digest_model(model=None):
    if not need_store():
        return 1
    st = lib.read_state_raw()
    if model is None:
        if not st or lib.state_problems(st):
            print("error: state.json missing or invalid (run doctor)", file=sys.stderr)
            return 1
        print(st["digest_model"])
        return 0
    if not lib.MODEL_RE.match(model):
        print("error: invalid model id %r" % model, file=sys.stderr)
        return 1
    st = st or {}
    if not isinstance(st.get("enabled"), bool):
        st["enabled"] = True
    st["digest_model"] = model
    lib.write_state(st)
    lib.log(NAME, "digest_model=%s" % model)
    print("digest_model = %s" % model)
    return 0


def dir_stats(path):
    n = size = 0
    if path.is_dir():
        for dp, _, fns in os.walk(str(path)):
            for fn in fns:
                if fn.startswith("."):
                    continue
                n += 1
                try:
                    size += os.path.getsize(os.path.join(dp, fn))
                except OSError:
                    pass
    return n, size


def cmd_status():
    if not need_store():
        return 1
    store = lib.store_dir()
    st = lib.read_state_raw()
    bad = (not st) or lib.state_problems(st)
    print("store:        %s" % store)
    print("enabled:      %s" % ("INVALID (treated as disabled)" if bad else str(st["enabled"]).lower()))
    print("digest_model: %s" % ("?" if bad else st["digest_model"]))
    n, size = dir_stats(store / "inbox")
    print("inbox:        %d file(s), %d bytes" % (n, size))
    try:
        txt = (store / "short-memory.md").read_text(encoding="utf-8")
        print("short-memory: %d line(s), %d bytes (cap %d lines x %d chars)"
              % (len(txt.splitlines()), len(txt.encode("utf-8")), lib.SHORT_MAX_LINES, lib.SHORT_MAX_COLS))
    except OSError:
        print("short-memory: missing")
    snap = lib.metrics_snapshot()
    print("long-memory:  %d page(s), %d bytes, %d tag(s)" % (snap["long_pages"], snap["long_bytes"], snap["tags"]))
    return 0


def cmd_metrics():
    if not need_store():
        return 1
    snap = lib.append_metrics()
    lib.log(NAME, "metrics %s" % json.dumps(snap))
    print(json.dumps(snap))
    return 0


def cmd_dashboard(args):
    target = HERE / "dashboard.py"
    if not target.is_file():
        print("error: dashboard.py not found at %s (reinstall the plugin)" % target, file=sys.stderr)
        return 1
    return subprocess.run([sys.executable, str(target)] + args).returncode


# ----------------------------------------------------------------- doctor
class Doctor:
    def __init__(self):
        self.fails = 0
        self.total = 0

    def check(self, name, ok, detail=""):
        self.total += 1
        if not ok:
            self.fails += 1
        print("%s  %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail and not ok else ""))
        return ok


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def hook_wired(settings, event, script_path):
    entries = ((settings.get("hooks") or {}).get(event)) if isinstance(settings.get("hooks"), dict) else None
    if not isinstance(entries, list):
        return False
    for e in entries:
        for h in (e.get("hooks") or []) if isinstance(e, dict) else []:
            if isinstance(h, dict) and any(str(p) in str(h.get("command", "")) for p in script_path):
                return True
    return False


def check_skill(d):
    sk = lib.skill_dir()
    d.check("skill dir exists", sk.is_dir(), str(sk))
    for rel in SKILL_FILES:
        d.check("skill file %s" % rel, (sk / rel).is_file(), "missing")
    for s in SCRIPTS:
        p = sk / "scripts" / s
        d.check("script %s exists and is executable" % s, p.is_file() and os.access(p, os.X_OK),
                "missing" if not p.is_file() else "not executable")
    marker = read(sk / ".brain-plugin")
    d.check("marker .brain-plugin non-empty", bool(marker and marker.strip()), "empty")


def check_hooks(d):
    sp = lib.claude_dir() / "settings.json"
    raw = read(sp)
    settings = None
    if raw is not None:
        try:
            settings = json.loads(raw)
        except ValueError:
            settings = None
    d.check("settings.json parses", isinstance(settings, dict), str(sp))
    settings = settings if isinstance(settings, dict) else {}
    for event, script in (("SessionStart", "session_start.py"), ("SessionEnd", "session_end.py")):
        paths = {lib.installed_skill_dir() / "scripts" / script, lib.skill_dir() / "scripts" / script}
        d.check("hook %s wired to %s" % (event, script), hook_wired(settings, event, paths),
                "not found in %s" % sp)


def check_store(d):
    store = lib.store_dir()
    d.check("store dir exists", store.is_dir(), str(store))
    for rel in STORE_DIRS:
        d.check("store dir %s/" % rel, (store / rel).is_dir(), "missing")
    for rel in STORE_FILES:
        d.check("store file %s" % rel, (store / rel).is_file(), "missing")
    st = lib.read_state_raw()
    probs = ["unreadable or not an object"] if st is None else lib.state_problems(st)
    d.check("state.json valid (enabled bool, digest_model non-empty string)", not probs, "; ".join(probs))
    # metrics
    mtxt = read(store / "metrics.jsonl")
    bad = []
    if mtxt is not None:
        for i, ln in enumerate(mtxt.splitlines(), 1):
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
                if not isinstance(o, dict) or any(k not in o for k in METRIC_KEYS):
                    bad.append(i)
            except ValueError:
                bad.append(i)
    d.check("metrics.jsonl exists and every line parses", mtxt is not None and not bad,
            "missing" if mtxt is None else "bad line(s): %s" % bad[:5])
    # short memory
    stxt = read(store / "short-memory.md")
    if stxt is None:
        d.check("short-memory.md caps and format", False, "missing")
    else:
        lines = stxt.splitlines()
        d.check("short-memory.md <= %d lines" % lib.SHORT_MAX_LINES, len(lines) <= lib.SHORT_MAX_LINES,
                "%d lines" % len(lines))
        long_l = [i for i, ln in enumerate(lines, 1) if len(ln) > lib.SHORT_MAX_COLS]
        d.check("short-memory.md lines <= %d chars" % lib.SHORT_MAX_COLS, not long_l, "line(s) %s" % long_l[:5])
        d.check("short-memory.md has no frontmatter", not (lines and lines[0].strip() == "---"), "starts with ---")


def check_reserved(d, name, text):
    try:
        fm, _ = lib.parse_frontmatter(text)
    except lib.FrontmatterError as exc:
        d.check("%s reserved frontmatter" % name, False, str(exc))
        return
    ok = (isinstance(fm, dict) and "type" not in fm and fm.get("okf_version") == "0.1"
          and isinstance(fm.get("generated"), dict))
    d.check("%s reserved frontmatter (okf_version 0.1, generated, no type)" % name, ok, "invalid")


def check_long_memory(d):
    root = lib.long_dir()
    if not root.is_dir():
        return
    pages = lib.list_pages()
    page_set = set(pages)
    for rel in pages:
        txt = read(root / rel)
        if txt is None:
            d.check("page %s valid OKF" % rel, False, "unreadable")
            continue
        try:
            fm, body = lib.parse_frontmatter(txt)
        except lib.FrontmatterError as exc:
            d.check("page %s valid OKF" % rel, False, str(exc))
            continue
        probs = []
        if fm is None:
            probs.append("no frontmatter")
        else:
            for k in ("type", "title", "description"):
                if not isinstance(fm.get(k), str) or not fm[k].strip():
                    probs.append("%s missing/empty" % k)
            if not isinstance(fm.get("tags"), list):
                probs.append("tags must be a list")
            if fm.get("status") not in LONG_STATUSES:
                probs.append("status must be one of %s (draft is inbox-only)" % "|".join(LONG_STATUSES))
            g = fm.get("generated")
            if not (isinstance(g, dict) and g.get("by") and g.get("at")):
                probs.append("generated needs by + at")
        d.check("page %s valid OKF" % rel, not probs, "; ".join(probs))
        if fm is not None:
            broken = []
            for t in lib.extract_links(body):
                r = lib.resolve_link(t, rel)
                if r is None:
                    continue
                if r.startswith("..") or not (root / r).is_file():
                    broken.append(t)
            d.check("page %s cross-links resolve" % rel, not broken, "broken: %s" % broken[:5])
    # reserved files
    for name in lib.RESERVED:
        txt = read(root / name)
        if txt is not None:
            check_reserved(d, name, txt)
    # index consistency
    itxt = read(root / "index.md")
    if itxt is not None:
        try:
            _, ibody = lib.parse_frontmatter(itxt)
        except lib.FrontmatterError:
            ibody = ""
        listed = []
        for ln in ibody.splitlines():
            m = INDEX_LINE_RE.match(ln)
            if m:
                r = lib.resolve_link(m.group(1), "index.md")
                if r is not None:
                    listed.append(r)
        missing = sorted(page_set - set(listed))
        extra = sorted(set(listed) - page_set)
        dup = len(listed) != len(set(listed))
        d.check("index.md lists exactly the existing pages", not (missing or extra or dup),
                "unlisted=%s stale=%s%s" % (missing[:5], extra[:5], " duplicates" if dup else ""))
    # log format
    ltxt = read(root / "log.md")
    if ltxt is not None:
        try:
            _, lbody = lib.parse_frontmatter(ltxt)
        except lib.FrontmatterError:
            lbody = ""
        hdrs = [ln for ln in lbody.splitlines() if ln.startswith("## ")]
        stamps = [LOG_HDR_RE.match(h).group(1) if LOG_HDR_RE.match(h) else None for h in hdrs]
        d.check("log.md headers are '## YYYY-MM-DD HH:MM'", all(stamps), "bad header(s)")
        good = [s for s in stamps if s]
        d.check("log.md newest-first", good == sorted(good, reverse=True), "not in descending order")


def cmd_doctor():
    d = Doctor()
    check_skill(d)
    check_hooks(d)
    check_store(d)
    check_long_memory(d)
    d.check("claude binary resolvable", lib.find_claude() is not None,
            "not found (set %s)" % lib.CLAUDE_BIN_ENV)
    print("doctor: %d/%d checks passed" % (d.total - d.fails, d.total))
    return 0 if d.fails == 0 else 1


# ----------------------------------------------------------------- dispatch
def main(argv):
    if argv == ["on"]:
        return cmd_set_enabled(True)
    if argv == ["off"]:
        return cmd_set_enabled(False)
    if argv == ["status"]:
        return cmd_status()
    if argv == ["digest", "model"]:
        return cmd_digest_model()
    if len(argv) == 3 and argv[:2] == ["digest", "model"]:
        return cmd_digest_model(argv[2])
    if argv == ["doctor"]:
        return cmd_doctor()
    if argv == ["metrics"]:
        return cmd_metrics()
    if argv and argv[0] == "dashboard":
        rest = argv[1:]
        r = [a for a in rest if a != "--no-open"]
        if len(rest) - len(r) <= 1 and (r == [] or (len(r) == 2 and r[0] == "--out")):
            return cmd_dashboard(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
