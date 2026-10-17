# WHY:  the hermetic suite proves the plumbing with a fake `claude`; only the real model can show
#       that the digest cookbook yields correct memory (§17.1) and that the brain is worth having
#       (§17.2). These checks cost money and are non-deterministic, so they are opt-in.
# WHAT: GATED, skipped unless BRAIN_LIVE_TESTS=1 (and a real `claude` resolves; the sandbox uses
#       tempdir AGENT_HOME / CLAUDE_CONFIG_DIR, so auth must work without the real config, e.g.
#       ANTHROPIC_API_KEY). Run: BRAIN_LIVE_TESTS=1 python3 -m unittest discover -s tests -p 'test_live.py'
#       (1) Digest fixtures: real `claude -p` digest via digest_runner on session_normal.jsonl;
#           asserts the PostgreSQL and Kafka facts land somewhere in long-memory, every page is
#           valid OKF, index/log/short-memory/inbox invariants hold, no forbidden (invented) term.
#       (2) Recall probes: fixed questions whose answers live only in the brain, asked headlessly
#           brain-on (session_start context + store access) and brain-off (disabled, no context);
#           prints both scores and requires on > off.
# WHO:  a human, on demand (BRAIN_LIVE_TESTS=1); never part of the default run.
import json
import os
import shutil
import subprocess
import unittest

from helpers import BrainCase, fixture, lib

LIVE = os.environ.get("BRAIN_LIVE_TESTS") == "1"
PLAN = json.loads(fixture("digest_plan.json").read_text())
PROBES = [  # (question, expected substring); answers exist only in the fixture transcript
    ("Which database does the Atlas project use? Answer in one short sentence.", "PostgreSQL"),
    ("Which event bus did we decide on for the Atlas project? Answer in one short sentence.", "Kafka"),
]


@unittest.skipUnless(LIVE, "live layer: set BRAIN_LIVE_TESTS=1 to run real `claude -p` (costs money)")
class LiveTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.env.pop("BRAIN_CLAUDE_BIN", None)  # use the real binary
        self.env.setdefault("BRAIN_DIGEST_TIMEOUT", "600")
        real = shutil.which("claude", path=self.env.get("PATH"))
        if not real:
            self.skipTest("no real `claude` binary on PATH")
        self.claude = real
        self.install()

    def digest(self, sid, fixture_name):
        path = self.put_transcript(sid, fixture_name)
        r = self.run_script("digest_runner.py", [sid, str(path)], timeout=700)
        self.assertEqual(r.returncode, 0, r.stderr + self.brain_log())

    def test_digest_fixture_correctness(self):
        self.digest("live-1", "session_normal.jsonl")
        self.assert_valid_okf_store()
        blob = "".join(p.read_text() for p in (self.store / "long-memory").rglob("*.md"))
        blob += (self.store / "short-memory.md").read_text()
        self.assertIn("PostgreSQL", blob)
        self.assertIn("Kafka", blob)
        self.assert_no_forbidden(PLAN["forbidden"])
        self.assertEqual(len(self.metrics_lines()), 2)

    def ask(self, question, brain_on):
        cmd = [self.claude, "-p", question]
        if brain_on:
            r = self.run_script("session_start.py", payload={"session_id": "probe", "source": "startup"})
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            cmd += ["--append-system-prompt", ctx, "--add-dir", str(self.store),
                    "--allowedTools", "Read,Glob,Grep"]
        env = dict(self.env, BRAIN_HEADLESS="1")
        cwd = self.store if brain_on else self.empty_dir()
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(cwd), timeout=300,
                           stdin=subprocess.DEVNULL)
        return r.stdout

    def empty_dir(self):
        d = self.tmp / "empty-cwd"
        d.mkdir(exist_ok=True)
        return d

    def score(self, brain_on):
        return sum(1 for q, want in PROBES if want.lower() in self.ask(q, brain_on).lower())

    def test_recall_probes_on_beats_off(self):
        self.digest("live-2", "session_normal.jsonl")
        on = self.score(True)
        self.cli("off")
        off = self.score(False)
        print("\nrecall probes: brain on %d/%d, brain off %d/%d" % (on, len(PROBES), off, len(PROBES)))
        self.assertGreater(on, off)


if __name__ == "__main__":
    unittest.main()
