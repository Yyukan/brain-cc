# WHY:  session_start runs on every Claude Code session; it must inject the brain context when
#       (and only when) the brain is healthy and enabled, and never emit or break otherwise.
# WHAT: checks the additionalContext JSON shape and content, store-path substitution, session
#       bookkeeping, and silence for: disabled, missing/corrupt state, missing store, and the
#       BRAIN_HEADLESS=1 recursion sentinel (§17.1 recursion fixture).
# WHO:  `python3 -m unittest discover -s tests`.
import json
import unittest

from helpers import BrainCase

PAYLOAD = {"session_id": "sess-start-1", "source": "startup", "hook_event_name": "SessionStart",
           "cwd": "/work/atlas", "transcript_path": "/nonexistent.jsonl"}


class SessionStartTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        (self.store / "short-memory.md").write_text("Prefers PostgreSQL.\nRuns tests before commit.\n")

    def start(self, env_extra=None, payload=PAYLOAD):
        return self.run_script("session_start.py", payload=payload, env_extra=env_extra)

    def assert_silent(self, r):
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_injects_expected_json_shape(self):
        r = self.start()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(list(out), ["hookSpecificOutput"])
        hso = out["hookSpecificOutput"]
        self.assertEqual(sorted(hso), ["additionalContext", "hookEventName"])
        self.assertEqual(hso["hookEventName"], "SessionStart")
        ctx = hso["additionalContext"]
        instr = (self.skill / "references" / "instructions.md").read_text().strip()
        self.assertTrue(ctx.startswith(instr.split("\n")[0]))
        self.assertIn("Prefers PostgreSQL.", ctx)
        self.assertTrue(ctx.rstrip().endswith("Runs tests before commit."))

    def test_store_path_substituted(self):
        ctx = json.loads(self.start().stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("~/.agents/brain", ctx)
        self.assertIn(str(self.store), ctx)

    def test_records_session_id(self):
        self.start()
        self.assertIn("sess-start-1", (self.store / ".sessions").read_text())

    def test_bad_session_id_not_recorded(self):
        self.start(payload=dict(PAYLOAD, session_id="../evil"))
        self.assertEqual((self.store / ".sessions").read_text(), "")

    def test_silent_when_disabled(self):
        self.assertEqual(self.cli("off").returncode, 0)
        self.assert_silent(self.start())
        self.assertEqual((self.store / ".sessions").read_text(), "")

    def test_silent_when_state_missing(self):
        (self.store / "state.json").unlink()
        self.assert_silent(self.start())

    def test_silent_when_state_corrupt(self):
        for bad in ("{not json", "[]", '{"enabled": "yes", "digest_model": "auto"}',
                    '{"enabled": true}', '{"enabled": true, "digest_model": ""}'):
            with self.subTest(state=bad):
                (self.store / "state.json").write_text(bad)
                self.assert_silent(self.start())

    def test_silent_when_store_missing(self):
        import shutil
        shutil.rmtree(str(self.store))
        self.assert_silent(self.start())

    def test_headless_sentinel_emits_nothing(self):
        self.assert_silent(self.start(env_extra={"BRAIN_HEADLESS": "1"}))
        self.assertEqual((self.store / ".sessions").read_text(), "")
        self.assertNotIn("session_start", self.brain_log())

    def test_garbage_stdin_does_not_crash(self):
        r = self.run_script("session_start.py", payload="not json at all")
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)  # still injects


if __name__ == "__main__":
    unittest.main()
