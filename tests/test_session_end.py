# WHY:  session_end decides whether to launch a slow, paid digest; the §17.1 fixtures demand it
#       never spawns for zero-turn sessions, unrecorded sessions, or inside a headless digest.
# WHAT: drives the SessionEnd hook with canned transcripts. The spawn is observed through the
#       fake claude's marker file (bounded poll). Negative cases assert the logged skip reason
#       synchronously and then that no marker ever appears.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import time
import unittest

from helpers import BrainCase, fixture, wait_for


class SessionEndTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.marker = self.tmp / "marker.json"
        self.xenv = {"BRAIN_FAKE_MARKER": str(self.marker)}

    def start(self, sid):
        r = self.run_script("session_start.py", payload={"session_id": sid, "source": "startup"})
        self.assertEqual(r.returncode, 0)

    def end(self, sid, transcript, env_extra=None):
        env = dict(self.xenv)
        env.update(env_extra or {})
        return self.run_script("session_end.py", payload={
            "session_id": sid, "transcript_path": str(transcript), "reason": "other",
            "hook_event_name": "SessionEnd"}, env_extra=env)

    def assert_no_spawn(self, r):
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        time.sleep(0.4)  # give a (wrongly) spawned child time to show up
        self.assertFalse(self.marker.exists(), "digest must not have been spawned")
        self.assertNotIn("spawned digest_runner", self.brain_log())

    def test_zero_turn_sessions_do_not_spawn(self):
        for name in ("session_zero_user.jsonl", "session_tool_result_only.jsonl", "session_ismeta.jsonl"):
            with self.subTest(fixture=name):
                sid = name.replace(".jsonl", "").replace("_", "-")
                path = self.put_transcript(sid, name)
                self.start(sid)
                r = self.end(sid, path)
                self.assert_no_spawn(r)
                self.assertIn("zero user turns", self.brain_log())

    def test_unrecorded_session_does_not_spawn(self):
        path = self.put_transcript("never-started", "session_normal.jsonl")
        r = self.end("never-started", path)
        self.assert_no_spawn(r)
        self.assertIn("not recorded", self.brain_log())

    def test_recorded_session_with_turns_spawns_detached_digest(self):
        path = self.put_transcript("sess-ok-1", "session_normal.jsonl")
        self.start("sess-ok-1")
        r = self.end("sess-ok-1", path)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertTrue(wait_for(self.marker.exists), "fake claude was never invoked")
        self.wait_runner_done()
        rec = json.loads(self.marker.read_text())
        self.assertEqual(rec["headless"], "1")
        self.assertTrue(any(f.endswith("-sess-ok-1.md") for f in rec["inbox"]), rec["inbox"])
        self.assertNotIn("sess-ok-1", (self.store / ".sessions").read_text())
        self.assertEqual(list((self.store / "inbox").glob("*.md")), [])

    def test_session_with_only_one_real_turn_among_noise_spawns(self):
        path = self.put_transcript("sess-mixed", "session_mixed.jsonl")
        self.start("sess-mixed")
        self.end("sess-mixed", path)
        self.assertTrue(wait_for(self.marker.exists))
        self.wait_runner_done()

    def test_headless_sentinel_emits_nothing_and_spawns_nothing(self):
        path = self.put_transcript("sess-hl", "session_normal.jsonl")
        self.start("sess-hl")
        r = self.end("sess-hl", path, env_extra={"BRAIN_HEADLESS": "1"})
        self.assert_no_spawn(r)
        self.assertIn("sess-hl", (self.store / ".sessions").read_text())
        self.assertNotIn("session_end", self.brain_log())

    def test_disabled_does_not_spawn(self):
        path = self.put_transcript("sess-off", "session_normal.jsonl")
        self.start("sess-off")
        self.cli("off")
        self.assert_no_spawn(self.end("sess-off", path))

    def test_missing_transcript_does_not_spawn(self):
        self.start("sess-gone")
        r = self.end("sess-gone", self.tmp / "nope.jsonl")
        self.assert_no_spawn(r)
        self.assertIn("transcript missing", self.brain_log())

    def test_invalid_session_id_does_not_spawn(self):
        r = self.run_script("session_end.py", payload={
            "session_id": "../x", "transcript_path": str(fixture("session_normal.jsonl"))},
            env_extra=self.xenv)
        self.assert_no_spawn(r)


if __name__ == "__main__":
    unittest.main()
