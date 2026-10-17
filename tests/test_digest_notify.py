# WHY:  the end-of-digest notification is the user's only feedback from a detached paid job;
#       it must be accurate (deterministic diff), safe (escaped AppleScript) and never harmful.
# WHAT: runs digest_runner.py with BRAIN_NOTIFY_CMD=fake_osascript.py and checks the recorded
#       title/subtitle/body for success (changes / no changes), failure, timeout, non-darwin,
#       digest_notification=false; unit-tests the AppleScript builder and size formatter.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import sys
import unittest

from helpers import BrainCase, FIXTURES, SRC_SCRIPTS

sys.path.insert(0, str(SRC_SCRIPTS))
import digest_runner as dr  # noqa: E402


class NotifyBuilderTests(unittest.TestCase):
    def test_escaping(self):
        s = dr.build_script('t"1', 'sub\\x', 'a "q" b\\ c\nline\x07two')
        self.assertEqual(s, 'display notification "a \\"q\\" b\\\\ c line two" '
                            'with title "t\\"1" subtitle "sub\\\\x"')

    def test_human_bytes(self):
        self.assertEqual(dr.human_bytes(512), "512 B")
        self.assertEqual(dr.human_bytes(14643), "14.3 KB")
        self.assertEqual(dr.human_bytes(3 * 1024 * 1024), "3.0 MB")


class NotifyTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.nlog = self.tmp / "notify.jsonl"
        self.xenv = {"BRAIN_NOTIFY_CMD": str(FIXTURES / "fake_osascript.py"),
                     "BRAIN_NOTIFY_PLATFORM": "darwin", "BRAIN_FAKE_NOTIFY_LOG": str(self.nlog)}

    def digest(self, sid, fixture_name=None, env_extra=None, text=None):
        path = self.put_transcript(sid, fixture_name, text=text)
        env = dict(self.xenv)
        env.update(env_extra or {})
        return self.run_script("digest_runner.py", [sid, str(path)], env_extra=env, timeout=60)

    def sent(self):
        if not self.nlog.exists():
            return []
        return [json.loads(l) for l in self.nlog.read_text().splitlines()]

    def one(self):
        recs = self.sent()
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual(recs[0]["argv"][0], "-e")
        return recs[0]["script"]

    def test_success_with_changes(self):
        self.add_page("projects/atlas/entities.md", title="Atlas entities")  # will be updated
        r = self.digest("s1", "session_normal.jsonl")
        self.assertEqual(r.returncode, 0, r.stderr + self.brain_log())
        s = self.one()
        self.assertIn('with title "🧠 brain · digest ✓"', s)
        self.assertRegex(s, r'subtitle "📥 1 session · \d+(\.\d)? (B|KB)"')
        self.assertIn("📚 ✚ projects/atlas/tasks ✎ projects/atlas/entities", s)
        self.assertNotIn(".md", s)
        self.assertIn("⚡ +1: Prefers PostgreSQL over MySQL for Atlas.", s)

    def test_success_no_changes(self):
        text = json.dumps({"type": "user", "cwd": "/w", "timestamp": "2026-10-15T12:00:00Z",
                           "message": {"role": "user", "content": "just chatting about the weather"}}) + "\n"
        r = self.digest("s2", text=text)
        self.assertEqual(r.returncode, 0)
        s = self.one()
        self.assertIn("digest ✓", s)
        self.assertIn("📚 no changes", s)
        self.assertNotIn("⚡", s)

    def test_failure(self):
        r = self.digest("s3", "session_normal.jsonl", {"BRAIN_FAKE_MODE": "fail", "BRAIN_FAKE_EXIT": "7"})
        self.assertEqual(r.returncode, 7)
        s = self.one()
        self.assertIn("digest ✗", s)
        self.assertIn("exit code 7 · inbox kept", s)
        self.assertIn("📥 1 session", s)

    def test_timeout(self):
        r = self.digest("s4", "session_normal.jsonl",
                        {"BRAIN_FAKE_MODE": "sleep", "BRAIN_FAKE_SLEEP": "60", "BRAIN_DIGEST_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 124)
        s = self.one()
        self.assertIn("digest ✗", s)
        self.assertIn("timed out after 1s · inbox kept", s)

    def test_non_darwin_is_silent_noop(self):
        r = self.digest("s5", "session_normal.jsonl", {"BRAIN_NOTIFY_PLATFORM": "linux"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.sent(), [])

    def test_missing_notifier_does_not_break_digest(self):
        r = self.digest("s6", "session_normal.jsonl", {"BRAIN_NOTIFY_CMD": str(self.tmp / "nope")})
        self.assertEqual(r.returncode, 0)

    def test_disabled_in_state_skips_all(self):
        self.assertEqual(self.cli("digest", "notification", "off").returncode, 0)
        self.assertEqual(self.digest("s7", "session_normal.jsonl").returncode, 0)
        self.assertEqual(self.digest("s8", "session_normal.jsonl",
                                     {"BRAIN_FAKE_MODE": "fail"}).returncode, 3)
        self.assertEqual(self.digest("s9", "session_normal.jsonl",
                                     {"BRAIN_FAKE_MODE": "sleep", "BRAIN_FAKE_SLEEP": "60",
                                      "BRAIN_DIGEST_TIMEOUT": "1"}).returncode, 124)
        self.assertEqual(self.sent(), [])


if __name__ == "__main__":
    unittest.main()
