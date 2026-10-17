# WHY:  install.sh is the only thing that writes to a user's real config; it must be idempotent,
#       never lose foreign settings, and never clobber a skill it does not own (Alex has one).
# WHAT: runs install.sh against tempdir AGENT_HOME / CLAUDE_CONFIG_DIR / HOME: fresh install,
#       idempotence, store untouched on re-run, settings merge, foreign dir/symlink refusal,
#       BRAIN_FORCE, invalid settings.json.
# WHO:  `python3 -m unittest discover -s tests`.
import json
import os
import unittest

from helpers import BrainCase, snapshot_tree


class InstallTests(BrainCase):
    def hooks(self):
        return json.loads(self.settings_path.read_text())["hooks"]

    def ours(self, event):
        return [h for e in self.hooks()[event] for h in e["hooks"] if "/scripts/session_" in h["command"]]

    def test_fresh_install(self):
        r = self.install()
        self.assertTrue((self.skill / ".brain-plugin").is_file())
        for s in ("brain_cli.py", "session_start.py", "session_end.py", "digest_runner.py", "capture.py",
                  "dashboard.py"):
            self.assertTrue(os.access(self.script(s), os.X_OK), s)
        self.assertEqual(json.loads((self.store / "state.json").read_text()),
                         {"enabled": True, "digest_model": "auto", "digest_notification": True})
        for rel in ("brain.log", ".sessions", "metrics.jsonl", "short-memory.md",
                    "long-memory/index.md", "long-memory/log.md"):
            self.assertTrue((self.store / rel).is_file(), rel)
        self.assertTrue((self.store / "inbox").is_dir())
        for d in ("projects", "team", "personal", "misc"):
            self.assertFalse((self.store / "long-memory" / d).exists(), d)
        self.assertEqual((self.store / "short-memory.md").read_text(), "")
        self.assertEqual(len(self.metrics_lines()), 1)
        self.assertEqual(len(self.ours("SessionStart")), 1)
        self.assertEqual(len(self.ours("SessionEnd")), 1)
        self.assertIn(str(self.skill), self.ours("SessionStart")[0]["command"])
        self.assertEqual([p.name for p in (self.claude_dir / "skills").iterdir()], ["brain"])
        self.assertIn("done", r.stdout)

    def test_idempotent_settings_and_skill(self):
        self.install()
        first = self.settings_path.read_bytes()
        self.install()
        self.install()
        self.assertEqual(self.settings_path.read_bytes(), first)
        self.assertEqual(len(self.ours("SessionStart")), 1)
        self.assertEqual(len(self.ours("SessionEnd")), 1)
        self.assertFalse((self.claude_dir / "settings.json.bak").exists(), "no .bak without prior file")

    def test_reinstall_restores_code(self):
        self.install()
        self.script("capture.py").write_text("# tampered\n")
        self.install()
        self.assertIn("WHY", self.script("capture.py").read_text())

    def test_store_untouched_on_rerun(self):
        self.install()
        (self.store / "state.json").write_text('{"enabled": false, "digest_model": "opus"}\n')
        (self.store / "short-memory.md").write_text("Prefers tabs.\n")
        (self.store / "inbox" / "2026-10-17-x.md").write_text("raw\n")
        self.add_page("projects/p/tasks.md", "Tasks")
        before = snapshot_tree(self.store)
        r = self.install()
        self.assertEqual(snapshot_tree(self.store), before)
        self.assertIn("untouched", r.stdout)

    def test_settings_merge_keeps_foreign(self):
        foreign = {
            "model": "opus",
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {
                "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo foreign"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
            },
        }
        original = json.dumps(foreign, indent=2)
        self.settings_path.write_text(original)
        self.install()
        self.install()
        s = json.loads(self.settings_path.read_text())
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"], foreign["permissions"])
        self.assertEqual(s["hooks"]["PreToolUse"], foreign["hooks"]["PreToolUse"])
        self.assertIn(foreign["hooks"]["SessionStart"][0], s["hooks"]["SessionStart"])
        self.assertEqual(len(self.ours("SessionStart")), 1)
        self.assertEqual(len(self.ours("SessionEnd")), 1)
        self.assertEqual((self.claude_dir / "settings.json.bak").read_text(), original)

    def test_settings_merge_replaces_stale_own_entry_without_duplicates(self):
        self.install()
        s = json.loads(self.settings_path.read_text())
        s["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 99  # drifted copy of our own entry
        self.settings_path.write_text(json.dumps(s))
        self.install()
        self.assertEqual(len(self.ours("SessionStart")), 1)
        self.assertEqual(self.ours("SessionStart")[0]["timeout"], 10)

    def test_invalid_settings_json_refused_and_untouched(self):
        self.settings_path.write_text("{not json")
        r = self.install(check=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.settings_path.read_text(), "{not json")

    def test_refuses_foreign_skill_dir(self):
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("mine\n")
        r = self.install(check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("BRAIN_FORCE", r.stderr)
        self.assertEqual((self.skill / "SKILL.md").read_text(), "mine\n")
        self.assertFalse(self.settings_path.exists())
        self.assertFalse(self.store.exists())

    def test_refuses_symlinked_foreign_skill(self):
        target = self.tmp / "agents-skills-brain"
        target.mkdir()
        (target / "SKILL.md").write_text("alex\n")
        self.skill.parent.mkdir(parents=True)
        self.skill.symlink_to(target)
        r = self.install(check=False)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.skill.is_symlink())
        self.assertEqual((target / "SKILL.md").read_text(), "alex\n")
        self.assertEqual(sorted(os.listdir(str(target))), ["SKILL.md"])

    def test_force_replaces_foreign_dir(self):
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("mine\n")
        self.install(env_extra={"BRAIN_FORCE": "1"})
        self.assertTrue((self.skill / ".brain-plugin").is_file())
        self.assertNotEqual((self.skill / "SKILL.md").read_text(), "mine\n")

    def test_force_replaces_symlink_but_never_its_target(self):
        target = self.tmp / "agents-skills-brain"
        target.mkdir()
        (target / "SKILL.md").write_text("alex\n")
        self.skill.parent.mkdir(parents=True)
        self.skill.symlink_to(target)
        self.install(env_extra={"BRAIN_FORCE": "1"})
        self.assertFalse(self.skill.is_symlink())
        self.assertTrue((self.skill / ".brain-plugin").is_file())
        self.assertEqual((target / "SKILL.md").read_text(), "alex\n")
        self.assertEqual(sorted(os.listdir(str(target))), ["SKILL.md"])


if __name__ == "__main__":
    unittest.main()
