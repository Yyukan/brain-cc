# WHY:  every test needs the same hermetic sandbox (tempdir AGENT_HOME / CLAUDE_CONFIG_DIR / HOME)
#       and the same §17.1 invariant checks; one module keeps them identical and keeps the
#       real ~/.claude and ~/.agents (incl. Alex's unrelated ~/.claude/skills/brain) untouched.
# WHAT: BrainCase base class (sandbox env, install, script runners, transcript helpers, polling),
#       OKF/digest invariant assertions shared by the hermetic and the live layer.
# WHO:  imported by every tests/test_*.py and tests/live/*.py.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_CLAUDE = FIXTURES / "fake_claude.py"
INSTALL_SH = REPO / "install.sh"
SRC_SCRIPTS = REPO / "skills" / "brain" / "scripts"

sys.path.insert(0, str(SRC_SCRIPTS))
import brain_lib as lib  # noqa: E402  (pure helpers only: parser, link resolver)

LOG_HDR_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})$")
REAL_DIRS = (Path.home() / ".claude", Path.home() / ".agents")


def fixture(name):
    return FIXTURES / name


def wait_for(pred, timeout=15.0, step=0.05):
    """Bounded poll. Returns True as soon as pred() is truthy, False on timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


def snapshot_tree(root):
    """{relative path: bytes} for every file under root (dirs recorded as None)."""
    out = {}
    for dp, dn, fn in os.walk(str(root)):
        for d in dn:
            out[os.path.relpath(os.path.join(dp, d), str(root))] = None
        for f in fn:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, str(root))] = Path(p).read_bytes()
    return out


class BrainCase(unittest.TestCase):
    """Sandboxed test case. self.tmp / self.agent_home / self.claude_dir / self.home are tempdirs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brain-test-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.agent_home = self.tmp / "agents"
        self.claude_dir = self.tmp / "claude"
        for d in (self.home, self.agent_home, self.claude_dir):
            d.mkdir()
        for real in REAL_DIRS:
            self.assertNotIn(real.resolve(), [self.tmp, *self.tmp.parents], "sandbox must not be the real dir")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BRAIN_") and k not in ("AGENT_HOME", "CLAUDE_CONFIG_DIR")}
        env.update(HOME=str(self.home), AGENT_HOME=str(self.agent_home),
                   CLAUDE_CONFIG_DIR=str(self.claude_dir), PYTHONDONTWRITEBYTECODE="1",
                   BRAIN_CLAUDE_BIN=str(FAKE_CLAUDE))
        self.env = env

    # ---------------------------------------------------------------- paths
    @property
    def store(self):
        return self.agent_home / "brain"

    @property
    def skill(self):
        return self.claude_dir / "skills" / "brain"

    @property
    def settings_path(self):
        return self.claude_dir / "settings.json"

    def script(self, name):
        return self.skill / "scripts" / name

    # ---------------------------------------------------------------- running things
    def run_cmd(self, cmd, stdin=None, env_extra=None, timeout=60):
        env = dict(self.env)
        env.update(env_extra or {})
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env,
                              cwd=str(self.tmp), timeout=timeout)

    def install(self, env_extra=None, check=True):
        r = self.run_cmd(["bash", str(INSTALL_SH)], env_extra=env_extra)
        if check:
            self.assertEqual(r.returncode, 0, "install failed: %s %s" % (r.stdout, r.stderr))
        return r

    def run_script(self, name, args=(), payload=None, env_extra=None, timeout=60):
        stdin = json.dumps(payload) if isinstance(payload, (dict, list)) else (payload or "")
        return self.run_cmd([sys.executable, str(self.script(name))] + list(args), stdin=stdin,
                            env_extra=env_extra, timeout=timeout)

    def cli(self, *args, env_extra=None):
        return self.run_script("brain_cli.py", args, env_extra=env_extra)

    # ---------------------------------------------------------------- transcripts / store
    def put_transcript(self, session_id, fixture_name=None, project="proj", text=None):
        d = self.claude_dir / "projects" / project
        d.mkdir(parents=True, exist_ok=True)
        dest = d / ("%s.jsonl" % session_id)
        if fixture_name:
            shutil.copyfile(str(fixture(fixture_name)), str(dest))
        else:
            dest.write_text(text, encoding="utf-8")
        return dest

    def brain_log(self):
        try:
            return (self.store / "brain.log").read_text(encoding="utf-8")
        except OSError:
            return ""

    def wait_runner_done(self, timeout=20):
        """Wait for a detached digest_runner (success path ends with the metrics log line)."""
        self.assertTrue(wait_for(lambda: "metrics exit code" in self.brain_log(), timeout),
                        "digest_runner did not finish; log:\n" + self.brain_log())

    def metrics_lines(self):
        return [json.loads(l) for l in (self.store / "metrics.jsonl").read_text().splitlines() if l.strip()]

    def pages(self):
        return lib.list_pages(self.store / "long-memory")

    def add_page(self, rel, title="Test page", extra_body="", fm_override=None, index=True):
        """Create a valid OKF page (and list it in index.md unless index=False)."""
        root = self.store / "long-memory"
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fm = fm_override if fm_override is not None else (
            "---\ntype: Concept\ntitle: %s\ndescription: About %s.\ntags: [t1]\nstatus: stable\n"
            "generated: { by: agent:claude-code, at: 2026-10-17T10:00:00+02:00 }\n---\n" % (title, title))
        p.write_text(fm + "\n# %s\n%s\n" % (title, extra_body), encoding="utf-8")
        if index:
            idx = root / "index.md"
            idx.write_text(idx.read_text() + "* [%s](/%s) - About %s.\n" % (title, rel, title), encoding="utf-8")
        return p

    # ---------------------------------------------------------------- §17.1 invariants
    def assert_valid_okf_store(self):
        """Every page valid OKF, links resolve, index lists exactly the pages, log header format,
        short-memory within caps, inbox empty. Shared with the live layer."""
        root = self.store / "long-memory"
        pages = lib.list_pages(root)
        for rel in pages:
            fm, body = lib.parse_frontmatter((root / rel).read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, "%s: no frontmatter" % rel)
            for k in ("type", "title", "description"):
                self.assertTrue(isinstance(fm.get(k), str) and fm[k].strip(), "%s: %s" % (rel, k))
            self.assertIsInstance(fm.get("tags"), list, rel)
            self.assertIn(fm.get("status"), ("stable", "deprecated"), rel)
            self.assertTrue(fm["generated"].get("by") and fm["generated"].get("at"), rel)
            for t in lib.extract_links(body):
                r = lib.resolve_link(t, rel)
                if r is not None:
                    self.assertTrue((root / r).is_file(), "%s: broken link %s" % (rel, t))
        # index
        _, ibody = lib.parse_frontmatter((root / "index.md").read_text(encoding="utf-8"))
        listed = []
        for ln in ibody.splitlines():
            m = re.match(r"^\s*[*-]\s+\[[^\]]*\]\(\s*<?([^)\s>]+)", ln)
            if m:
                listed.append(lib.resolve_link(m.group(1), "index.md"))
        self.assertEqual(sorted(listed), sorted(pages), "index.md must list exactly the pages")
        # log
        lfm, lbody = lib.parse_frontmatter((root / "log.md").read_text(encoding="utf-8"))
        self.assertNotIn("type", lfm)
        hdrs = [l for l in lbody.splitlines() if l.startswith("## ")]
        for h in hdrs:
            self.assertRegex(h, LOG_HDR_RE)
        stamps = [LOG_HDR_RE.match(h).group(1) for h in hdrs]
        self.assertEqual(stamps, sorted(stamps, reverse=True), "log.md must be newest-first")
        # short memory
        sm = (self.store / "short-memory.md").read_text(encoding="utf-8")
        lines = sm.splitlines()
        self.assertLessEqual(len(lines), 100)
        self.assertTrue(all(len(l) <= 200 for l in lines))
        self.assertFalse(lines and lines[0].strip() == "---", "short-memory must have no frontmatter")
        # inbox
        self.assertEqual([p.name for p in (self.store / "inbox").glob("*.md")], [], "inbox must end empty")
        return pages

    def assert_no_forbidden(self, words):
        """No forbidden (never-in-fixtures) term anywhere in long-memory or short-memory."""
        blobs = [(self.store / "short-memory.md").read_text(encoding="utf-8")]
        for dp, _, fn in os.walk(str(self.store / "long-memory")):
            blobs += [Path(dp, f).read_text(encoding="utf-8") for f in fn]
        for w in words:
            for b in blobs:
                self.assertNotIn(w, b, "invented fact: %s" % w)
