# WHY:  digest_runner is the detached, paid step that turns an inbox into long-term memory;
#       §17.1 requires the digest invariants to hold and failures to never lose raw captures.
# WHAT: runs digest_runner.py against the fake claude (BRAIN_CLAUDE_BIN) on canned transcripts:
#       success (facts on expected pages, valid OKF, index == pages, log header, short-memory
#       caps, nothing invented, inbox empty, metrics line appended), the claude invocation
#       contract, failure (inbox kept, no metrics), timeout (exit 124), and abort paths.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import unittest

from helpers import BrainCase, fixture, lib

PLAN = json.loads(fixture("digest_plan.json").read_text())


class DigestRunnerTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.marker = self.tmp / "marker.json"
        self.xenv = {"BRAIN_FAKE_MARKER": str(self.marker)}

    def digest(self, sid, fixture_name, env_extra=None, timeout=60):
        path = self.put_transcript(sid, fixture_name)
        env = dict(self.xenv)
        env.update(env_extra or {})
        return self.run_script("digest_runner.py", [sid, str(path)], env_extra=env, timeout=timeout)

    def page(self, rel):
        return (self.store / "long-memory" / rel).read_text()

    def test_success_applies_fixture_facts(self):
        old = ["Old fact %03d" % i for i in range(100)]  # full short-memory: forces an eviction
        (self.store / "short-memory.md").write_text("\n".join(old) + "\n")
        seed_index = (self.store / "long-memory" / "index.md").read_text()
        seed_log = (self.store / "long-memory" / "log.md").read_text()
        r = self.digest("sess-1", "session_normal.jsonl")
        self.assertEqual(r.returncode, 0, r.stderr + self.brain_log())
        # facts on the expected pages
        pages = self.assert_valid_okf_store()
        self.assertEqual(sorted(pages), ["projects/atlas/entities.md", "projects/atlas/tasks.md"])
        self.assertIn("Atlas uses PostgreSQL as its database.", self.page("projects/atlas/tasks.md"))
        self.assertIn("Atlas event bus decision: Kafka.", self.page("projects/atlas/entities.md"))
        self.assertIn("](entities.md)", self.page("projects/atlas/tasks.md"))
        # index/log updated, reserved frontmatter untouched
        idx, log = (self.store / "long-memory" / n for n in ("index.md", "log.md"))
        self.assertNotEqual(idx.read_text(), seed_index)
        self.assertNotEqual(log.read_text(), seed_log)
        self.assertEqual(idx.read_text().split("---")[1], seed_index.split("---")[1])
        self.assertEqual(log.read_text().split("---")[1], seed_log.split("---")[1])
        self.assertIn("* [Atlas tasks](/projects/atlas/tasks.md) - ", idx.read_text())
        # short memory: promoted fact in, oldest evicted, exactly at the cap
        sm = (self.store / "short-memory.md").read_text().splitlines()
        self.assertEqual(len(sm), 100)
        self.assertIn("Prefers PostgreSQL over MySQL for Atlas.", sm)
        self.assertNotIn("Old fact 000", sm)
        self.assertIn("Old fact 099", sm)
        # metrics: seed + one digest snapshot reflecting the new state
        m = self.metrics_lines()
        self.assertEqual(len(m), 2)
        self.assertEqual((m[-1]["long_pages"], m[-1]["short_lines"], m[-1]["tags"]), (2, 100, 3))
        self.assertGreater(m[-1]["long_bytes"], 0)
        self.assertIn("claude exit code 0", self.brain_log())

    def test_no_invented_facts(self):
        self.digest("sess-2", "session_normal.jsonl")
        transcript = fixture("session_normal.jsonl").read_text()
        for w in PLAN["forbidden"]:
            self.assertNotIn(w, transcript, "forbidden term must be absent from the fixture")
        self.assert_no_forbidden(PLAN["forbidden"])
        self.assertIn("projects/atlas/entities.md", self.pages())  # normal fixture has Kafka
        # only the PostgreSQL material: the Kafka page must not appear
        r = self.digest("sess-3", "session_mixed.jsonl")
        self.assertEqual(r.returncode, 0)
        sm = (self.store / "short-memory.md").read_text()
        self.assertEqual(sm.count("Prefers PostgreSQL over MySQL for Atlas."), 1)

    def test_transcript_without_facts_creates_nothing(self):
        text = json.dumps({"type": "user", "cwd": "/w", "timestamp": "2026-10-15T12:00:00Z",
                           "message": {"role": "user", "content": "just chatting about the weather"}}) + "\n"
        path = self.put_transcript("sess-chat", text=text)
        r = self.run_script("digest_runner.py", ["sess-chat", str(path)], env_extra=self.xenv)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.pages(), [])
        self.assertEqual((self.store / "short-memory.md").read_text(), "")
        self.assertEqual(list((self.store / "inbox").glob("*.md")), [])

    def test_only_kafka_material_yields_only_entities_page(self):
        text = json.dumps({"type": "user", "cwd": "/w", "timestamp": "2026-10-15T12:00:00Z",
                           "message": {"role": "user", "content": "We chose Kafka for Atlas."}}) + "\n"
        path = self.put_transcript("sess-k", text=text)
        self.assertEqual(self.run_script("digest_runner.py", ["sess-k", str(path)], env_extra=self.xenv).returncode, 0)
        self.assertEqual(self.pages(), ["projects/atlas/entities.md"])
        self.assert_valid_okf_store()

    def test_second_digest_merges_and_keeps_invariants(self):
        self.digest("sess-a", "session_mixed.jsonl")
        self.digest("sess-b", "session_normal.jsonl")
        self.assert_valid_okf_store()
        tasks = self.page("projects/atlas/tasks.md")
        self.assertEqual(tasks.count("Atlas uses PostgreSQL as its database."), 1)
        log = (self.store / "long-memory" / "log.md").read_text()
        self.assertGreaterEqual(log.count("\n## "), 1)
        self.assertEqual(len(self.metrics_lines()), 3)

    def test_claude_invocation_contract(self):
        self.digest("sess-c", "session_normal.jsonl")
        rec = json.loads(self.marker.read_text())
        argv = rec["argv"]
        self.assertEqual(argv[0], "-p")
        self.assertTrue(argv[1].startswith("Brain store root: %s " % self.store))
        self.assertIn("# Digest cookbook", argv[1])
        self.assertNotIn("--model", argv)
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.store))
        tools = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertIn("Read", tools)
        self.assertFalse([t for t in tools if t.startswith("Bash(rm")], tools)
        self.assertEqual(rec["cwd"], str(self.store.resolve()))
        self.assertEqual(rec["headless"], "1")
        self.assertEqual(rec["store_root"], str(self.store))

    def test_digest_model_passed_when_not_auto(self):
        self.assertEqual(self.cli("digest", "model", "claude-sonnet-5-5").returncode, 0)
        self.digest("sess-m", "session_normal.jsonl")
        argv = json.loads(self.marker.read_text())["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")

    def test_failure_keeps_inbox_and_skips_metrics(self):
        r = self.digest("sess-f", "session_normal.jsonl", {"BRAIN_FAKE_MODE": "fail", "BRAIN_FAKE_EXIT": "7"})
        self.assertEqual(r.returncode, 7)
        self.assertEqual(len(list((self.store / "inbox").glob("*-sess-f.md"))), 1)
        self.assertEqual(len(self.metrics_lines()), 1)
        self.assertEqual(self.pages(), [])
        self.assertIn("claude exit code 7", self.brain_log())

    def test_timeout_exits_124_and_keeps_inbox(self):
        r = self.digest("sess-t", "session_normal.jsonl",
                        {"BRAIN_FAKE_MODE": "sleep", "BRAIN_FAKE_SLEEP": "60", "BRAIN_DIGEST_TIMEOUT": "1"},
                        timeout=30)
        self.assertEqual(r.returncode, 124)
        self.assertEqual(len(list((self.store / "inbox").glob("*-sess-t.md"))), 1)
        self.assertEqual(len(self.metrics_lines()), 1)
        self.assertIn("TIMEOUT", self.brain_log())

    def test_zero_turn_transcript_aborts_without_claude(self):
        r = self.digest("sess-z", "session_tool_result_only.jsonl")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.marker.exists())

    def test_disabled_aborts_without_claude(self):
        self.cli("off")
        r = self.digest("sess-d", "session_normal.jsonl")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list((self.store / "inbox").glob("*.md")), [])

    def test_missing_claude_binary_aborts_keeping_capture(self):
        r = self.digest("sess-n", "session_normal.jsonl", {"BRAIN_CLAUDE_BIN": str(self.tmp / "nope")})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(list((self.store / "inbox").glob("*-sess-n.md"))), 1)

    def test_bad_args_usage(self):
        self.assertEqual(self.run_script("digest_runner.py", ["only-one"]).returncode, 2)
        self.assertEqual(self.run_script("digest_runner.py", []).returncode, 2)


if __name__ == "__main__":
    unittest.main()
