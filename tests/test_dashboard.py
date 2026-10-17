# WHY:  the dashboard embeds user-authored page content in an HTML file opened via file://;
#       a stray `</script>` in a title would break (or hijack) the page.
# WHAT: `dashboard.py --no-open --out` and `brain_cli dashboard`: HTML with embedded JSON data,
#       </script> / < escaping, read-only store, works when disabled, missing store exits 1.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import re
import unittest

from helpers import BrainCase, snapshot_tree

EVIL = '</script><script>alert(1)</script><!-- &'


class DashboardTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.out = self.tmp / "out" / "dash.html"

    def embedded(self, html):
        m = re.search(r'<script id="brain-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(m, "embedded data block not found")
        return json.loads(m.group(1))

    def test_generates_html_with_embedded_data(self):
        (self.store / "short-memory.md").write_text("Prefers PostgreSQL.\n")
        self.add_page("projects/p/tasks.md", "Tasks")
        before = snapshot_tree(self.store)
        r = self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(self.out))
        html = self.out.read_text()
        self.assertTrue(html.lstrip().lower().startswith("<!doctype html"))
        data = self.embedded(html)
        self.assertEqual(data["totals"]["pages"], 1)
        self.assertEqual(data["pages"][0]["title"], "Tasks")
        self.assertEqual(data["state"]["enabled"], True)
        self.assertIn("Prefers PostgreSQL.", json.dumps(data["short"]))
        self.assertEqual(len(data["metrics"]), 1)
        self.assertNotIn("__BRAIN_DATA__", html)
        after = snapshot_tree(self.store)
        after.pop("brain.log", None), before.pop("brain.log", None)
        self.assertEqual(after, before, "dashboard must not modify the store (except the log)")

    def test_script_close_tag_is_escaped(self):
        tpl_closers = (self.skill / "assets" / "dashboard_template.html").read_text().lower().count("</script>")
        fm = ("---\ntype: Concept\ntitle: %s\ndescription: %s\ntags: [t]\nstatus: stable\n"
              "generated: { by: agent:claude-code, at: 2026-10-17T10:00:00+02:00 }\n---\n"
              % (json.dumps(EVIL), json.dumps(EVIL)))
        self.add_page("evil/page.md", fm_override=fm, index=False)
        (self.store / "short-memory.md").write_text(EVIL + "\n")
        r = self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        html = self.out.read_text()
        self.assertEqual(html.lower().count("</script>"), tpl_closers)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<!-- &", html)
        data = self.embedded(html)  # round-trips to the original text
        self.assertEqual(data["pages"][0]["title"], EVIL)

    def test_quoted_titles_round_trip_escapes(self):
        title = 'He said "hi" \\ back \u00e9 \U0001F600'
        fm = ("---\ntype: Concept\ntitle: %s\ndescription: %s\ntags: [t]\nstatus: stable\n"
              "generated: { by: agent:claude-code, at: 2026-10-17T10:00:00+02:00 }\n---\n"
              % (json.dumps(title), json.dumps(title, ensure_ascii=False)))
        self.add_page("q/page.md", fm_override=fm, index=False)
        r = self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        page = self.embedded(self.out.read_text())["pages"][0]
        self.assertEqual(page["title"], title)

    def test_default_output_path_in_store(self):
        r = self.run_script("dashboard.py", ["--no-open"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.store / "dashboard.html").is_file())
        self.assertEqual(r.stdout.strip(), str(self.store / "dashboard.html"))

    def test_works_when_disabled(self):
        self.cli("off")
        r = self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.embedded(self.out.read_text())["state"]["enabled"], False)

    def test_survives_corrupt_state_and_bad_metrics(self):
        (self.store / "state.json").write_text("{nope")
        (self.store / "metrics.jsonl").write_text("garbage\n")
        r = self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_via_brain_cli(self):
        r = self.cli("dashboard", "--no-open", "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.out.is_file())

    def test_missing_store_exits_1(self):
        import shutil
        shutil.rmtree(str(self.store))
        for runner in (lambda: self.run_script("dashboard.py", ["--no-open", "--out", str(self.out)]),
                       lambda: self.cli("dashboard", "--no-open", "--out", str(self.out))):
            r = runner()
            self.assertEqual(r.returncode, 1)
            self.assertIn("store not found", r.stderr)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
