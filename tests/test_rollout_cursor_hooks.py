"""DB-free tests for Cursor hook rollout/merge behavior."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rollout_cursor_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("rollout_cursor_hooks", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRolloutCursorHooks(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        (self.project / ".claude" / "hooks").mkdir(parents=True)
        (self.project / ".claude" / "hooks" / "session_recall.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_discovery_requires_bootstrapped_session_hook(self):
        other = Path(self.temp.name) / "unrelated"
        other.mkdir()
        self.assertEqual(self.mod.discover(Path(self.temp.name)), [self.project])

    def test_merge_preserves_unrelated_cursor_hooks(self):
        existing = {
            "version": 1,
            "hooks": {
                "afterFileEdit": [{"command": ".cursor/hooks/format.sh"}],
                "beforeShellExecution": [{"command": ".cursor/hooks/network-gate.sh"}],
            },
        }
        template = json.loads(
            (REPO / "templates" / ".cursor" / "hooks.json").read_text(encoding="utf-8")
        )
        merged = self.mod.merged_hooks(existing, template)
        edit_commands = [entry["command"] for entry in merged["hooks"]["afterFileEdit"]]
        self.assertIn(".cursor/hooks/format.sh", edit_commands)
        self.assertIn("python3 .cursor/hooks/shared_memory_drift_queue.py", edit_commands)
        self.assertEqual(
            merged["hooks"]["beforeShellExecution"], existing["hooks"]["beforeShellExecution"]
        )

    def test_merge_preserves_unrelated_claude_settings(self):
        existing = {
            "hooks": {
                "PostToolUse": [{"matcher": "Write", "hooks": [{"command": "format"}]}]
            },
            "permissions": {"allow": ["Bash(pytest*)"]},
        }
        template = json.loads(
            (REPO / "templates" / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        merged = self.mod.merged_claude_settings(existing, template)
        self.assertEqual(merged["permissions"], existing["permissions"])
        self.assertEqual(len(merged["hooks"]["PostToolUse"]), 2)
        self.assertIn(
            "pattern-search",
            merged["hooks"]["PostToolUse"][1]["matcher"],
        )

    def test_apply_installs_hooks_and_is_idempotent(self):
        status, _ = self.mod.install(self.project, apply=True, stamp="test")
        self.assertEqual(status, "updated")
        self.assertTrue((self.project / ".cursor" / "hooks.json").is_file())
        mode = (
            self.project / ".cursor" / "hooks" / self.mod.CURSOR_HOOK_FILES[0]
        ).stat().st_mode
        self.assertTrue(mode & 0o100)
        self.assertIn(
            ".cursor/.shared-memory-sessions/",
            (self.project / ".gitignore").read_text(encoding="utf-8"),
        )

        status, changes = self.mod.install(self.project, apply=True, stamp="test2")
        self.assertEqual((status, changes), ("unchanged", []))


if __name__ == "__main__":
    unittest.main()
