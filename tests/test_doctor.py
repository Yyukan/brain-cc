# WHY:  `brain_cli doctor` is the single "is my install and store correct" check; it is only
#       trustworthy if it passes on a healthy install and fails loudly on each corruption.
# WHAT: passes on a fresh install and on a digested store; fails (exit 1, FAIL line naming the
#       problem) for bad frontmatter, index mismatch, broken cross-link, short-memory over
#       caps, bad metrics line, missing hook wiring, and a few structural losses.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import unittest

from helpers import BrainCase


class DoctorTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()

    def doctor(self):
        return self.cli("doctor")

    def assert_fails(self, needle):
        r = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        self.assertTrue(any(needle in l for l in fails), "no FAIL containing %r in:\n%s" % (needle, r.stdout))
        return r

    def test_fresh_install_passes(self):
        r = self.doctor()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("FAIL", r.stdout)
        last = r.stdout.strip().splitlines()[-1]
        n = int(last.split()[1].split("/")[0])
        self.assertEqual(last, "doctor: %d/%d checks passed" % (n, n))

    def test_digested_store_passes(self):
        self.digest_fixture()
        r = self.doctor()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("projects/atlas/tasks.md valid OKF", r.stdout)

    def digest_fixture(self):
        path = self.put_transcript("doc-1", "session_normal.jsonl")
        r = self.run_script("digest_runner.py", ["doc-1", str(path)])
        self.assertEqual(r.returncode, 0, self.brain_log())

    def test_good_page_with_links_passes(self):
        self.add_page("a/one.md", "One", extra_body="See [two](two.md) and [web](https://example.com) [x](#top).")
        self.add_page("a/two.md", "Two", extra_body="Back to [one](/a/one.md).")
        self.assertEqual(self.doctor().returncode, 0, self.doctor().stdout)

    def test_bad_frontmatter(self):
        for name, fm in (
            ("unterminated", "---\ntype: Concept\ntitle: T\n"),
            ("no frontmatter", ""),
            ("missing type", "---\ntitle: T\ndescription: d\ntags: [a]\nstatus: stable\n"
                             "generated: { by: x, at: y }\n---\n"),
            ("bad status", "---\ntype: Concept\ntitle: T\ndescription: d\ntags: [a]\nstatus: weird\n"
                           "generated: { by: x, at: y }\n---\n"),
            ("tags not a list", "---\ntype: Concept\ntitle: T\ndescription: d\n"
                           "tags: a\nstatus: stable\ngenerated: { by: x, at: y }\n---\n"),
        ):
            with self.subTest(case=name):
                p = self.add_page("bad/page.md", "Bad", fm_override=fm)
                self.assert_fails("page bad/page.md valid OKF")
                p.unlink()
                idx = self.store / "long-memory" / "index.md"
                idx.write_text("\n".join(l for l in idx.read_text().splitlines() if "bad/page.md" not in l) + "\n")
                self.assertEqual(self.doctor().returncode, 0, self.doctor().stdout)

    def test_draft_status_rejected_in_long_memory_but_valid_in_inbox(self):
        draft = ("---\ntype: Concept\ntitle: T\ndescription: d\ntags: [a]\nstatus: draft\n"
                 "generated: { by: x, at: y }\n---\n")
        self.add_page("d/draft.md", "Draft", fm_override=draft)
        self.assert_fails("page d/draft.md valid OKF")
        (self.store / "long-memory" / "d" / "draft.md").unlink()
        idx = self.store / "long-memory" / "index.md"
        idx.write_text("\n".join(l for l in idx.read_text().splitlines() if "d/draft.md" not in l) + "\n")
        self.put_transcript("inb-1", "session_normal.jsonl")
        self.assertEqual(self.run_script("capture.py", ["--session", "inb-1"]).returncode, 0)
        self.assertIn("status: draft", next((self.store / "inbox").glob("*.md")).read_text())
        self.assertEqual(self.doctor().returncode, 0, self.doctor().stdout)

    def test_index_mismatch(self):
        self.add_page("a/one.md", "One", index=False)
        self.assert_fails("index.md lists exactly")
        idx = self.store / "long-memory" / "index.md"
        idx.write_text(idx.read_text() + "* [One](/a/one.md) - d\n* [Ghost](/a/ghost.md) - d\n")
        self.assert_fails("index.md lists exactly")

    def test_broken_cross_link(self):
        self.add_page("a/one.md", "One", extra_body="See [nowhere](missing.md).")
        self.assert_fails("page a/one.md cross-links resolve")
        self.add_page("a/two.md", "Two", extra_body="See [escape](/../../etc/passwd).")
        self.assert_fails("page a/two.md cross-links resolve")

    def test_short_memory_over_caps(self):
        sm = self.store / "short-memory.md"
        sm.write_text("\n".join("fact %d" % i for i in range(101)) + "\n")
        self.assert_fails("short-memory.md <= 100 lines")
        sm.write_text("x" * 201 + "\n")
        self.assert_fails("short-memory.md lines <= 200 chars")
        sm.write_text("---\ntype: Note\n---\nfact\n")
        self.assert_fails("short-memory.md has no frontmatter")
        sm.write_text("a\n" * 100)
        self.assertEqual(self.doctor().returncode, 0, self.doctor().stdout)

    def test_bad_metrics_line(self):
        m = self.store / "metrics.jsonl"
        good = m.read_text()
        for bad in ("not json\n", '{"ts": "x"}\n', "[1]\n"):
            with self.subTest(line=bad):
                m.write_text(good + bad)
                self.assert_fails("metrics.jsonl")
        m.write_text(good)
        self.assertEqual(self.doctor().returncode, 0)

    def test_missing_hook_wiring(self):
        s = json.loads(self.settings_path.read_text())
        for event, script in (("SessionStart", "session_start.py"), ("SessionEnd", "session_end.py")):
            with self.subTest(event=event):
                saved = s["hooks"].pop(event)
                self.settings_path.write_text(json.dumps(s))
                self.assert_fails("hook %s wired to %s" % (event, script))
                s["hooks"][event] = saved
                self.settings_path.write_text(json.dumps(s))
                self.assertEqual(self.doctor().returncode, 0, self.doctor().stdout)
        self.settings_path.write_text("{broken")
        self.assert_fails("settings.json parses")
        self.settings_path.unlink()
        self.assert_fails("settings.json parses")

    def test_log_format_and_order(self):
        log = self.store / "long-memory" / "log.md"
        base = log.read_text()
        log.write_text(base + "\n## 2026-10-17 10:00\n\n* a\n\n## 2026-10-18 09:00\n\n* b\n")
        self.assert_fails("newest-first")
        log.write_text(base + "\n## 2026-10-17\n\n* a\n")
        self.assert_fails("log.md headers")

    def test_reserved_files_must_not_have_type(self):
        idx = self.store / "long-memory" / "index.md"
        idx.write_text('---\ntype: Concept\nokf_version: "0.1"\ngenerated: { by: x, at: y }\n---\n')
        self.assert_fails("index.md reserved frontmatter")

    def test_state_invalid_and_missing_store_parts(self):
        (self.store / "state.json").write_text('{"enabled": "yes", "digest_model": "auto"}')
        self.assert_fails("state.json valid")
        self.install()  # store is untouched by install, so repair by hand
        (self.store / "state.json").write_text('{"enabled": true, "digest_model": "auto"}')
        (self.store / "inbox").rmdir()
        self.assert_fails("store dir inbox/")

    def test_missing_skill_files_and_not_executable(self):
        (self.skill / "references" / "digest.md").unlink()
        self.assert_fails("skill file references/digest.md")
        self.install()
        self.script("capture.py").chmod(0o644)
        self.assert_fails("script capture.py")

    def test_claude_binary_unresolvable(self):
        r = self.run_script("brain_cli.py", ["doctor"], env_extra={"BRAIN_CLAUDE_BIN": str(self.tmp / "nope")})
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  claude binary resolvable", r.stdout)


if __name__ == "__main__":
    unittest.main()
