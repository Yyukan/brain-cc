# WHY:  capture turns raw Claude Code transcripts into inbox notes the digest consumes; wrong
#       turn counting, non-deterministic names or capturing the digest's own sessions would
#       corrupt the brain or loop forever.
# WHAT: both modes (`N` and `--session <id> [--transcript]`), deterministic filename and
#       overwrite on re-capture, OKF Note/draft frontmatter, exclusion of transcripts whose cwd
#       is the store, zero-turn/meta/tool_result handling, and bad-argument exit codes.
# WHO:  `python3 -m unittest discover -s tests`.
import datetime
import unittest

from helpers import BrainCase, fixture, lib

FIRST_USER_TS = "2026-10-15T12:01:00.000+00:00"  # first real user turn in session_normal.jsonl


def local_date(ts):
    return datetime.datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d")


class CaptureTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.inbox = self.store / "inbox"

    def inbox_names(self):
        return sorted(p.name for p in self.inbox.glob("*.md"))

    def test_session_mode_finds_transcript_by_id(self):
        self.put_transcript("abc-123", "session_normal.jsonl")
        r = self.run_script("capture.py", ["--session", "abc-123"])
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = "%s-abc-123.md" % local_date(FIRST_USER_TS)
        self.assertEqual(self.inbox_names(), [expected])
        self.assertIn(expected, r.stdout)
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith("captured 1 session(s)"))

    def test_session_mode_with_explicit_transcript_and_content(self):
        path = self.put_transcript("abc-124", "session_normal.jsonl", project="elsewhere")
        r = self.run_script("capture.py", ["--session", "abc-124", "--transcript", str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        note = (self.inbox / ("%s-abc-124.md" % local_date(FIRST_USER_TS))).read_text()
        fm, body = lib.parse_frontmatter(note)
        self.assertEqual(fm["type"], "Note")
        self.assertEqual(fm["status"], "draft")
        self.assertEqual(fm["tags"], ["inbox", "session"])
        self.assertEqual(fm["generated"]["by"], "process:capture")
        self.assertEqual(fm["session_id"], "abc-124")
        self.assertEqual(fm["cwd"], "/work/atlas")
        self.assertEqual(fm["turns"], "3")  # meta + tool_result lines are not turns
        self.assertIn("I prefer PostgreSQL over MySQL", body)
        self.assertIn("We decided to use Kafka", body)
        self.assertNotIn("local-command", body)
        self.assertNotIn("/clear", body)  # isMeta line excluded

    def test_recapture_overwrites_same_file(self):
        self.put_transcript("abc-125", "session_normal.jsonl")
        for _ in range(2):
            self.assertEqual(self.run_script("capture.py", ["--session", "abc-125"]).returncode, 0)
        self.assertEqual(len(self.inbox_names()), 1)

    def test_transcript_is_only_read(self):
        path = self.put_transcript("abc-126", "session_normal.jsonl")
        before = path.read_bytes()
        self.run_script("capture.py", ["--session", "abc-126"])
        self.assertEqual(path.read_bytes(), before)

    def test_zero_turn_session_not_captured(self):
        for name in ("session_zero_user.jsonl", "session_tool_result_only.jsonl", "session_ismeta.jsonl"):
            with self.subTest(fixture=name):
                self.put_transcript("zero-1", name)
                r = self.run_script("capture.py", ["--session", "zero-1"])
                self.assertEqual(r.returncode, 1)
                self.assertEqual(self.inbox_names(), [])

    def test_last_n_mode_excludes_store_cwd_and_zero_turn(self):
        self.put_transcript("good-1", "session_normal.jsonl")
        self.put_transcript("good-2", "session_mixed.jsonl", project="other")
        self.put_transcript("zero-1", "session_zero_user.jsonl")
        normal = fixture("session_normal.jsonl").read_text()
        self.put_transcript("brain-own", project="store", text=normal.replace("/work/atlas", str(self.store)))
        self.put_transcript("brain-sub", project="store2",
                            text=normal.replace("/work/atlas", str(self.store / "inbox")))
        r = self.run_script("capture.py", ["10"])
        self.assertEqual(r.returncode, 0, r.stderr)
        names = self.inbox_names()
        self.assertEqual(len(names), 2, names)
        self.assertTrue(any(n.endswith("-good-1.md") for n in names))
        self.assertTrue(any(n.endswith("-good-2.md") for n in names))
        self.assertFalse(any("brain-" in n for n in names))
        last = r.stdout.strip().splitlines()[-1]
        self.assertIn("captured 2 session(s)", last)
        self.assertIn("3 skipped", last)

    def test_last_n_respects_limit(self):
        self.put_transcript("good-1", "session_normal.jsonl")
        self.put_transcript("good-2", "session_mixed.jsonl", project="other")
        r = self.run_script("capture.py", ["1"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.inbox_names()), 1)

    def test_table_header_and_summary_last(self):
        self.put_transcript("good-1", "session_normal.jsonl")
        lines = self.run_script("capture.py", ["5"]).stdout.strip().splitlines()
        self.assertEqual(lines[0].split(), ["id", "date", "turns", "size", "file"])
        self.assertTrue(lines[-1].startswith("captured "))

    def test_bad_args_exit_nonzero(self):
        self.put_transcript("abc-1", "session_normal.jsonl")
        cases = [[], ["0"], ["-3"], ["abc"], ["--session"], ["--session", "a", "b"],
                 ["--session", "abc-1", "--bogus", "x"], ["1", "2"]]
        for args in cases:
            with self.subTest(args=args):
                r = self.run_script("capture.py", args)
                self.assertNotEqual(r.returncode, 0)
                self.assertTrue(r.stderr.strip())
        self.assertEqual(self.inbox_names(), [])

    def test_invalid_or_unknown_session_exit_nonzero(self):
        for sid in ("../etc/passwd", "no-such-session", "a b"):
            with self.subTest(sid=sid):
                self.assertNotEqual(self.run_script("capture.py", ["--session", sid]).returncode, 0)
        self.assertEqual(self.inbox_names(), [])

    def test_missing_store_exit_nonzero(self):
        import shutil
        shutil.rmtree(str(self.store))
        self.assertNotEqual(self.run_script("capture.py", ["3"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
