#!/usr/bin/env python3
# WHY:  every brain script needs the same primitives (paths, fail-closed state, logging,
#       atomic writes, OKF parsing, metrics); one stdlib-only module keeps them identical.
# WHAT: path resolution (AGENT_HOME / CLAUDE_CONFIG_DIR aware), state.json loading that
#       fails closed, brain.log writer, atomic file write, minimal OKF frontmatter parser,
#       markdown link extraction/resolution, transcript user-turn detection, metrics snapshot.
# WHO:  imported by session_start, session_end, digest_runner, capture and brain_cli.
"""Shared helpers for the second-brain plugin (Python 3 stdlib only)."""

import datetime
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

HEADLESS_ENV = "BRAIN_HEADLESS"
CLAUDE_BIN_ENV = "BRAIN_CLAUDE_BIN"
DEFAULT_STORE_TEXT = "~/.agents/brain"
RESERVED = ("index.md", "log.md")
SHORT_MAX_LINES = 100
SHORT_MAX_COLS = 200
SESSIONS_KEEP = 500
STATUSES = ("stable", "deprecated", "draft")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]/-]{0,99}$")


# ----------------------------------------------------------------- paths
def _env_path(name, default):
    val = os.environ.get(name)
    if val:
        return Path(os.path.expanduser(val))
    return default


def agent_home():
    return _env_path("AGENT_HOME", Path.home() / ".agents")


def claude_dir():
    return _env_path("CLAUDE_CONFIG_DIR", Path.home() / ".claude")


def store_dir():
    return agent_home() / "brain"


def skill_dir():
    """Skill directory this module lives in (scripts/ parent)."""
    return Path(__file__).resolve().parent.parent


def installed_skill_dir():
    return claude_dir() / "skills" / "brain"


def is_headless():
    return bool(os.environ.get(HEADLESS_ENV))


def inject_store_path(text):
    """Replace literal ~/.agents/brain with the real store path when it differs."""
    real = str(store_dir())
    if Path(real) == Path.home() / ".agents" / "brain":
        return text
    return text.replace(DEFAULT_STORE_TEXT, real)


# ----------------------------------------------------------------- time/log
def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(script, msg):
    """Append '<ISO ts> <script> <message>' to brain.log. Never raises."""
    try:
        store = store_dir()
        if not store.is_dir():
            return
        msg = " ".join(str(msg).split())
        with open(store / "brain.log", "a", encoding="utf-8") as fh:
            fh.write("%s %s %s\n" % (now_iso(), script, msg))
    except OSError:
        pass


# ----------------------------------------------------------------- io
def atomic_write(path, text):
    """Write text to path via temp file in the same dir + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_stdin_json():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


# ----------------------------------------------------------------- state
def read_state_raw():
    """Parsed state.json dict, or None if missing/unparsable/not an object."""
    try:
        data = json.loads((store_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def state_problems(state):
    """List of reasons a state dict is invalid (empty list = valid)."""
    if not isinstance(state, dict):
        return ["not a JSON object"]
    out = []
    if not isinstance(state.get("enabled"), bool):
        out.append("'enabled' must be a bool")
    dm = state.get("digest_model")
    if not isinstance(dm, str) or not dm.strip():
        out.append("'digest_model' must be a non-empty string")
    if "digest_notification" in state and not isinstance(state["digest_notification"], bool):
        out.append("'digest_notification' must be a bool when present")
    return out


def load_state():
    """Valid state dict, or None (fail closed)."""
    st = read_state_raw()
    if st is None or state_problems(st):
        return None
    return st


def is_enabled():
    st = load_state()
    return bool(st and st["enabled"] is True)


def write_state(state):
    atomic_write(store_dir() / "state.json", json.dumps(state, indent=2) + "\n")


# ----------------------------------------------------------------- claude binary
def find_claude():
    override = os.environ.get(CLAUDE_BIN_ENV)
    if override:
        return override if (os.path.isfile(override) and os.access(override, os.X_OK)) else None
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                 Path("/usr/local/bin/claude")):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


# ----------------------------------------------------------------- sessions bookkeeping
def record_session(session_id):
    path = store_dir() / ".sessions"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = [ln for ln in lines if ln.strip() and ln.split()[-1] != session_id]
    lines.append("%s %s" % (now_iso(), session_id))
    atomic_write(path, "\n".join(lines[-SESSIONS_KEEP:]) + "\n")


def session_recorded(session_id):
    try:
        lines = (store_dir() / ".sessions").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(ln.split() and ln.split()[-1] == session_id for ln in lines)


def forget_session(session_id):
    path = store_dir() / ".sessions"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    keep = [ln for ln in lines if ln.strip() and ln.split()[-1] != session_id]
    atomic_write(path, ("\n".join(keep) + "\n") if keep else "")


# ----------------------------------------------------------------- transcripts
def iter_transcript(path):
    """Yield parsed JSON objects from a transcript .jsonl (read-only, skips bad lines)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def is_user_turn(obj):
    """type=='user', not isMeta, content non-empty string or has a non-tool_result block."""
    if obj.get("type") != "user" or obj.get("isMeta"):
        return False
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content)
    return False


def has_user_turn(path):
    try:
        return any(is_user_turn(o) for o in iter_transcript(path))
    except OSError:
        return False


# ----------------------------------------------------------------- frontmatter (OKF subset)
class FrontmatterError(ValueError):
    pass


def _split_top(s, sep=","):
    """Split s on sep outside quotes/brackets/braces."""
    parts, depth, quote, cur = [], 0, None, []
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _scalar(s):
    s = s.strip()
    if not s:
        return ""
    if s[0] == '"':
        try:
            val, end = json.JSONDecoder().raw_decode(s)
        except ValueError:
            raise FrontmatterError("bad double-quoted string: %s" % s)
        if s[end:].strip() and not s[end:].strip().startswith("#"):
            raise FrontmatterError("trailing text after string: %s" % s)
        return val
    if s[0] == "'":
        m = re.match(r"^'((?:[^']|'')*)'\s*(#.*)?$", s)
        if not m:
            raise FrontmatterError("bad single-quoted string: %s" % s)
        return m.group(1).replace("''", "'")
    if s[0] == "[":
        if not s.endswith("]"):
            raise FrontmatterError("unterminated list: %s" % s)
        inner = s[1:-1].strip()
        return [_scalar(p) for p in _split_top(inner) if p.strip()] if inner else []
    if s[0] == "{":
        if not s.endswith("}"):
            raise FrontmatterError("unterminated map: %s" % s)
        inner = s[1:-1].strip()
        out = {}
        for p in _split_top(inner) if inner else []:
            if not p.strip():
                continue
            if ":" not in p:
                raise FrontmatterError("bad map entry: %s" % p)
            k, v = p.split(":", 1)
            out[k.strip()] = _scalar(v)
        return out
    return re.sub(r"\s+#.*$", "", s).strip()


def parse_frontmatter(text):
    """Return (dict|None, body). None when the text has no frontmatter block.
    Raises FrontmatterError for malformed frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end = i
            break
    if end is None:
        raise FrontmatterError("frontmatter not terminated by '---'")
    block = lines[1:end]
    body = "\n".join(lines[end + 1:])
    data = {}
    i = 0
    key_re = re.compile(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$")
    while i < len(block):
        raw = block[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw[0] in " \t":
            raise FrontmatterError("unexpected indentation: %r" % raw)
        m = key_re.match(raw.rstrip())
        if not m:
            raise FrontmatterError("bad line: %r" % raw)
        key, val = m.group(1), m.group(2)
        i += 1
        if val is not None and val.strip():
            data[key] = _scalar(val)
            continue
        # block value: following indented lines
        children = []
        while i < len(block) and (block[i][:1] in (" ", "\t") or not block[i].strip()):
            if block[i].strip():
                children.append(block[i].strip())
            i += 1
        if not children:
            data[key] = ""
        elif all(c.startswith("- ") or c == "-" for c in children):
            data[key] = [_scalar(c[1:]) for c in children]
        elif not any(c.startswith("-") for c in children):
            sub = {}
            for c in children:
                mm = key_re.match(c)
                if not mm:
                    raise FrontmatterError("bad nested line: %r" % c)
                sub[mm.group(1)] = _scalar(mm.group(2) or "")
            data[key] = sub
        else:
            raise FrontmatterError("mixed block value for %r" % key)
    return data, body


def yaml_quote(s):
    """Double-quoted YAML scalar (JSON string syntax is valid YAML)."""
    return json.dumps(str(s), ensure_ascii=False)


# ----------------------------------------------------------------- links
_FENCE_RE = re.compile(r"(```|~~~).*?(\1|\Z)", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def extract_links(body):
    """Raw link targets from markdown body (code spans/fences ignored)."""
    body = _FENCE_RE.sub("", body)
    body = _INLINE_CODE_RE.sub("", body)
    return _LINK_RE.findall(body)


def resolve_link(target, page_rel):
    """Resolve an OKF link to a bundle-relative posix path.
    Returns None for ignorable targets (external scheme, pure anchor).
    Returns a path starting with '../' when the target escapes the bundle root."""
    if _SCHEME_RE.match(target) or target.startswith("#"):
        return None
    target = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    if not target:
        return None
    if target.startswith("/"):
        rel = posixpath.normpath(target.lstrip("/"))
    else:
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), target))
    return rel


# ----------------------------------------------------------------- long memory
def long_dir():
    return store_dir() / "long-memory"


def list_pages(root=None):
    """Bundle-relative posix paths of all non-reserved .md files under long-memory/."""
    root = Path(root) if root else long_dir()
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.endswith(".md") or fn.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), str(root)).replace(os.sep, "/")
            if rel in RESERVED:
                continue
            out.append(rel)
    return out


def metrics_snapshot():
    store = store_dir()
    short_lines = 0
    try:
        short_lines = len((store / "short-memory.md").read_text(encoding="utf-8").splitlines())
    except OSError:
        pass
    pages = list_pages()
    total, tags = 0, set()
    for rel in pages:
        p = long_dir() / rel
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            total += p.stat().st_size
            fm, _ = parse_frontmatter(raw)
        except (OSError, FrontmatterError):
            continue
        t = (fm or {}).get("tags")
        if isinstance(t, list):
            tags.update(str(x) for x in t if str(x).strip())
    return {"ts": now_utc(), "short_lines": short_lines, "long_pages": len(pages),
            "long_bytes": total, "tags": len(tags)}


def append_metrics(snapshot=None):
    snap = snapshot or metrics_snapshot()
    path = store_dir() / "metrics.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap) + "\n")
    return snap
