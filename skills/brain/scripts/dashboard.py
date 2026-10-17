#!/usr/bin/env python3
# WHY:  Give a human a read-only visual overview of the brain store (short memory
#       gauges, growth chart, force-directed graph of long-memory pages) without
#       running any server or touching the network.
# WHAT: Reads the store ($AGENT_HOME/brain, default ~/.agents/brain), embeds the
#       data as JSON into assets/dashboard_template.html, writes a self-contained
#       STORE/dashboard.html (or --out) atomically and opens it via file://.
#       Never modifies store content. Stdlib only.
# WHO:  `brain_cli.py dashboard [--no-open] [--out PATH]`, or a human directly.
"""Generate the static brain dashboard."""
import argparse
import datetime
import json
import math
import os
import pathlib
import posixpath
import re
import sys
import tempfile
import urllib.parse
import webbrowser

SCRIPT = "dashboard"
SHORT_LINE_CAP = 100
SHORT_WIDTH_CAP = 200
RESERVED = ("index.md", "log.md")
PLACEHOLDER = "__BRAIN_DATA__"


def store_path():
    home = os.environ.get("AGENT_HOME") or os.path.join(os.path.expanduser("~"), ".agents")
    return os.path.join(home, "brain")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(store, msg):
    try:
        with open(os.path.join(store, "brain.log"), "a", encoding="utf-8") as f:
            f.write("%s %s %s\n" % (now_iso(), SCRIPT, msg))
    except OSError:
        pass


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---- minimal OKF frontmatter parser (YAML subset per contract) -------------

def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            val = json.loads(s)
            if isinstance(val, str):
                return val
        except ValueError:
            pass
        return s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _split_top(s):
    """Split on commas outside quotes/brackets."""
    parts, depth, quote, cur = [], 0, None, []
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _split_kv(s):
    """Split 'key: value' at the first colon outside quotes; None if no colon."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(s) or s[i + 1] in " \t"):
            return s[:i].strip(), s[i + 1:].strip()
    return None


def _scalar_or_inline(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_scalar_or_inline(p) for p in _split_top(v[1:-1])]
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for p in _split_top(v[1:-1]):
            kv = _split_kv(p.strip())
            if kv is None:
                raise ValueError("bad inline map")
            out[_unquote(kv[0])] = _scalar_or_inline(kv[1])
        return out
    return _unquote(v)


def parse_frontmatter(text):
    """Return (meta dict, body str, error str|None)."""
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text, "no frontmatter"
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end = i
            break
    if end is None:
        return {}, text, "unterminated frontmatter"
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    meta = {}
    block = lines[1:end]
    i = 0
    try:
        while i < len(block):
            raw = block[i].rstrip()
            i += 1
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw[0] in " \t":
                raise ValueError("unexpected indent")
            kv = _split_kv(raw)
            if kv is None:
                raise ValueError("expected key: value")
            key, val = kv
            if val == "" or val.startswith("#"):
                items, sub = [], {}
                while i < len(block) and (not block[i].strip() or block[i][0] in " \t"):
                    ln = block[i].strip()
                    i += 1
                    if not ln or ln.startswith("#"):
                        continue
                    if ln.startswith("- ") or ln == "-":
                        if sub:
                            raise ValueError("mixed list and map")
                        items.append(_scalar_or_inline(ln[1:]))
                    else:
                        if items:
                            raise ValueError("mixed list and map")
                        skv = _split_kv(ln)
                        if skv is None:
                            raise ValueError("bad nested line")
                        sub[_unquote(skv[0])] = _scalar_or_inline(skv[1])
                meta[key] = items if items else (sub if sub else "")
            else:
                meta[key] = _scalar_or_inline(val)
    except ValueError as e:
        return meta, body, "frontmatter parse error: %s" % e
    return meta, body, None


# ---- link extraction --------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.S | re.M)
_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_EXT = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*:|//)")


def extract_links(body, page_rel, root_files):
    """Return list of {target, resolved}. target is a bundle-relative path."""
    out, seen = [], set()
    clean = _FENCE.sub("", body)
    page_dir = posixpath.dirname(page_rel)
    for m in _LINK.finditer(clean):
        href = m.group(1).strip()
        if not href or href.startswith("#") or _EXT.match(href):
            continue
        href = urllib.parse.unquote(href.split("#", 1)[0].split("?", 1)[0])
        if not href:
            continue
        if href.startswith("/"):
            target = posixpath.normpath(href.lstrip("/"))
        else:
            target = posixpath.normpath(posixpath.join(page_dir, href))
        if target == ".." or target.startswith("../") or target == ".":
            target, ok = href, False
        else:
            ok = target in root_files
        if target in seen:
            continue
        seen.add(target)
        out.append({"target": target, "resolved": ok})
    return out


# ---- store readers ------------------------------------------------------------

def load_state(store):
    try:
        st = json.loads(read_text(os.path.join(store, "state.json")))
        enabled = st.get("enabled") is True  # fail closed
        model = st.get("digest_model")
        return {"enabled": enabled, "digest_model": model if isinstance(model, str) and model else "auto",
                "error": None}
    except (OSError, ValueError, AttributeError) as e:
        return {"enabled": False, "digest_model": "auto", "error": "state.json unreadable: %s" % e}


def load_short(store):
    try:
        text = read_text(os.path.join(store, "short-memory.md"))
    except OSError:
        text = ""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    longest = max([len(l) for l in lines] or [0])
    return {"text": text, "lines": len(lines), "longest": longest,
            "line_cap": SHORT_LINE_CAP, "width_cap": SHORT_WIDTH_CAP}


def load_pages(store):
    root = os.path.join(store, "long-memory")
    files = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for fn in sorted(fns):
            if not fn.endswith(".md") or fn.startswith("."):
                continue
            full = os.path.join(dp, fn)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files[rel] = full
    pages, bad_read = [], 0
    for rel in sorted(files):
        if rel in RESERVED:
            continue
        try:
            raw = read_text(files[rel])
            size = os.path.getsize(files[rel])
        except OSError:
            bad_read += 1
            continue
        meta, body, err = parse_frontmatter(raw)
        if err is None and not (isinstance(meta.get("type"), str) and meta.get("type").strip()):
            err = "missing type"
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags] if tags else []
        tags = [str(t) for t in tags if isinstance(t, (str, int, float)) and str(t)] if isinstance(tags, list) else []
        gen = meta.get("generated")
        if isinstance(gen, dict):
            generated = {"by": str(gen.get("by", "")), "at": str(gen.get("at", ""))}
        else:
            generated = {"by": "", "at": str(gen) if gen else ""}
        def s(k):
            v = meta.get(k, "")
            return v if isinstance(v, str) else json.dumps(v)
        pages.append({
            "path": rel,
            "title": s("title") or posixpath.basename(rel),
            "description": s("description"),
            "type": s("type"),
            "tags": tags,
            "status": s("status"),
            "generated": generated,
            "meta": {k: (v if isinstance(v, (str, list, dict)) else str(v)) for k, v in meta.items()},
            "body": body,
            "size": size,
            "links": extract_links(body, rel, set(files)),
            "fm_ok": err is None,
            "fm_error": err,
        })
    return pages, bad_read


def load_metrics(store):
    series, bad = [], 0
    try:
        text = read_text(os.path.join(store, "metrics.jsonl"))
    except OSError:
        return series, bad
    for ln in text.split("\n"):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
            pt = {"ts": str(d["ts"])}
            for k in ("short_lines", "long_pages", "long_bytes", "tags"):
                v = d.get(k, 0)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(k)
                pt[k] = v
            series.append(pt)
        except (ValueError, KeyError, TypeError, AttributeError):
            bad += 1
    return series, bad


def count_inbox(store):
    try:
        return sum(1 for f in os.listdir(os.path.join(store, "inbox")) if f.endswith(".md"))
    except OSError:
        return 0


def build_data(store):
    pages, bad_read = load_pages(store)
    series, bad_metrics = load_metrics(store)
    tags = sorted({t for p in pages for t in p["tags"]})
    return {
        "generated_at": now_iso(),
        "store": store,
        "state": load_state(store),
        "short": load_short(store),
        "pages": pages,
        "tags": tags,
        "totals": {"pages": len(pages), "bytes": sum(p["size"] for p in pages), "tags": len(tags)},
        "metrics": series,
        "metrics_skipped": bad_metrics,
        "pages_unreadable": bad_read,
        "inbox": count_inbox(store),
    }


def embed(template, data):
    js = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    js = js.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("template must contain %s exactly once" % PLACEHOLDER)
    return template.replace(PLACEHOLDER, js)


def atomic_write(path, content):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dashboard.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dashboard.py", description="Generate the static brain dashboard.")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--out", help="output path (default: STORE/dashboard.html)")
    args = ap.parse_args(argv)

    store = store_path()
    if not os.path.isdir(store):
        sys.stderr.write("dashboard: brain store not found at %s (run install.sh first)\n" % store)
        return 1
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets",
                            "dashboard_template.html")
    try:
        template = read_text(tpl_path)
    except OSError as e:
        sys.stderr.write("dashboard: cannot read template %s: %s\n" % (os.path.normpath(tpl_path), e))
        log(store, "ERROR template unreadable: %s" % e)
        return 1
    try:
        data = build_data(store)
        html = embed(template, data)
        out = os.path.abspath(os.path.expanduser(args.out)) if args.out else os.path.join(store, "dashboard.html")
        atomic_write(out, html)
    except (OSError, ValueError) as e:
        sys.stderr.write("dashboard: failed: %s\n" % e)
        log(store, "ERROR %s" % e)
        return 1
    log(store, "generated %s pages=%d metrics=%d skipped_metrics=%d" % (
        out, len(data["pages"]), len(data["metrics"]), data["metrics_skipped"]))
    print(out)
    if not args.no_open:
        try:
            webbrowser.open(pathlib.Path(out).as_uri())
        except Exception as e:  # browser failure must not fail generation
            sys.stderr.write("dashboard: could not open browser: %s\n" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
