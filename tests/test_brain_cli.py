# WHY:  brain_cli is the user's control surface (on/off, model, status); a wrong toggle either
#       silently disables the brain or leaves a paid digest running against the wrong model.
# WHAT: on / off / status / digest model get+set (valid and invalid ids) / metrics / usage error.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import sys
import unittest

from helpers import BrainCase


class BrainCliTests(BrainCase):
    def setUp(self):
        super().setUp()
        self.install()

    def state(self):
        return json.loads((self.store / "state.json").read_text())

    def test_off_then_on(self):
        r = self.cli("off")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "brain disabled"))
        self.assertEqual(self.state(), {"enabled": False, "digest_model": "auto", "digest_notification": True})
        r = self.cli("on")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "brain enabled"))
        self.assertEqual(self.state(), {"enabled": True, "digest_model": "auto", "digest_notification": True})

    def test_on_repairs_corrupt_state(self):
        (self.store / "state.json").write_text("{garbage")
        self.assertEqual(self.cli("on").returncode, 0)
        self.assertEqual(self.state(), {"enabled": True, "digest_model": "auto"})

    def test_digest_notification_show_on_off(self):
        self.assertEqual(self.cli("digest", "notification").stdout.strip(), "on")  # scaffold/missing = on
        r = self.cli("digest", "notification", "off")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "digest_notification = off"))
        self.assertEqual(self.state(), {"enabled": True, "digest_model": "auto", "digest_notification": False})
        self.assertEqual(self.cli("digest", "notification").stdout.strip(), "off")
        self.assertIn("digest_notification: off", self.cli("status").stdout)
        self.assertEqual(self.cli("digest", "notification", "on").returncode, 0)
        self.assertTrue(self.state()["digest_notification"])
        self.assertEqual(self.cli("digest", "notification", "maybe").returncode, 2)
        self.assertEqual(self.cli("notify").returncode, 2)

    def test_digest_notification_missing_key_valid_and_keeps_other_keys(self):
        (self.store / "state.json").write_text('{"enabled": false, "digest_model": "x-model"}')
        self.assertEqual(self.cli("digest", "notification").stdout.strip(), "on")
        self.assertEqual(self.cli("doctor").returncode, 0, self.cli("doctor").stdout)
        self.cli("digest", "notification", "off")
        self.assertEqual(self.state(), {"enabled": False, "digest_model": "x-model", "digest_notification": False})

    def test_status(self):
        (self.store / "short-memory.md").write_text("a\nb\n")
        (self.store / "inbox" / "x.md").write_text("raw")
        out = self.cli("status").stdout
        self.assertIn(str(self.store), out)
        self.assertIn("enabled:      true", out)
        self.assertIn("digest_model: auto", out)
        self.assertIn("inbox:        1 file(s)", out)
        self.assertIn("short-memory: 2 line(s)", out)
        self.assertIn("long-memory:  0 page(s)", out)
        self.cli("off")
        self.assertIn("enabled:      false", self.cli("status").stdout)

    def test_status_flags_invalid_state(self):
        (self.store / "state.json").write_text("{nope")
        r = self.cli("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("INVALID", r.stdout)

    def test_digest_model_get_and_set(self):
        self.assertEqual(self.cli("digest", "model").stdout.strip(), "auto")
        r = self.cli("digest", "model", "claude-sonnet-5-5")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.cli("digest", "model").stdout.strip(), "claude-sonnet-5-5")
        self.assertEqual(self.state()["enabled"], True)
        self.cli("off")
        self.cli("digest", "model", "opus")
        self.assertEqual(self.state(), {"enabled": False, "digest_model": "opus", "digest_notification": True})

    def test_digest_model_rejects_invalid_id(self):
        for bad in ("-rf", "a b", "x;y", "$(id)", ""):
            with self.subTest(model=bad):
                r = self.cli("digest", "model", bad)
                self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.state()["digest_model"], "auto")

    def test_digest_model_get_with_invalid_state_fails(self):
        (self.store / "state.json").write_text("[]")
        self.assertEqual(self.cli("digest", "model").returncode, 1)

    def test_metrics_appends_snapshot(self):
        r = self.cli("metrics")
        self.assertEqual(r.returncode, 0)
        snap = json.loads(r.stdout)
        self.assertEqual(sorted(snap), ["long_bytes", "long_pages", "short_lines", "tags", "ts"])
        self.assertEqual(len(self.metrics_lines()), 2)
        self.assertEqual(self.metrics_lines()[-1], snap)

    def test_usage_error(self):
        for args in ([], ["bogus"], ["on", "extra"], ["digest"], ["digest", "model", "a", "b"],
                     ["digest", "other"], ["dashboard", "--out"], ["dashboard", "--bogus"]):
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("usage:", r.stderr)

    def test_lib_has_no_deprecation_warnings(self):
        code = ("import sys; sys.path.insert(0, %r); import brain_lib as l; "
                "assert l.resolve_link('a.md#x?y', 'p/q.md') == 'p/a.md'; "
                "assert l.resolve_link('/a/b.md#x', 'p/q.md') == 'a/b.md'" % str(self.skill / "scripts"))
        r = self.run_cmd([sys.executable, "-W", "error::DeprecationWarning", "-c", code])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_store_fails(self):
        import shutil
        shutil.rmtree(str(self.store))
        for args in (["on"], ["off"], ["status"], ["metrics"], ["digest", "model"]):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 1)


if __name__ == "__main__":
    unittest.main()
