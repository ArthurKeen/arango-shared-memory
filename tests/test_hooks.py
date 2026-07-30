"""DB-free tests for the project-side hooks (stdlib unittest).

Covers: drift_queue.py marker behavior (code vs PRD edits), the drift stop gate's
block/allow/bypass rails, and session_recall.py's CLAUDE.md parsing + fail-open
guarantee. Run from the repo root:  python3 -m unittest discover tests
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")
DRIFT_QUEUE = os.path.join(HOOKS, "drift_queue.py")
STOP_GATE = os.path.join(HOOKS, "drift_stop_gate.sh")
SESSION_RECALL = os.path.join(HOOKS, "session_recall.py")

CLAUDE_MD = """# PROJECT: Demo
- PROJECT_ID: demo-api
- PROJECT_TYPE: web-api
- PRD_FILE: docs/PRD.md
"""


def _load_session_recall():
    spec = importlib.util.spec_from_file_location("session_recall", SESSION_RECALL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TmpProject(unittest.TestCase):
    """Base: a temp project dir with a rendered CLAUDE.md."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = self.dir.name
        with open(os.path.join(self.cwd, "CLAUDE.md"), "w") as fh:
            fh.write(CLAUDE_MD)

    def tearDown(self):
        self.dir.cleanup()

    def queue_files(self):
        qdir = os.path.join(self.cwd, ".prd-drift-queue")
        return sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []


class TestDriftQueue(TmpProject):
    def run_hook(self, payload):
        return subprocess.run(
            [sys.executable, DRIFT_QUEUE],
            input=json.dumps(payload) if not isinstance(payload, str) else payload,
            capture_output=True, text=True, cwd=self.cwd)

    def test_source_edit_queues_plain_marker(self):
        proc = self.run_hook({"tool_input": {"file_path": "src/auth/jwt.ts"}})
        self.assertEqual(proc.returncode, 0)
        markers = self.queue_files()
        self.assertEqual(len(markers), 1)
        self.assertFalse(markers[0].startswith("prd_"))
        self.assertIn("Implementation file modified", proc.stdout)

    def test_prd_edit_queues_prd_marker(self):
        proc = self.run_hook({"tool_input": {"file_path": "docs/PRD.md"}})
        self.assertEqual(proc.returncode, 0)
        markers = self.queue_files()
        self.assertEqual(len(markers), 1)
        self.assertTrue(markers[0].startswith("prd_"))
        self.assertIn("Requirements may have changed", proc.stdout)

    def test_non_source_edit_is_ignored(self):
        proc = self.run_hook({"tool_input": {"file_path": "notes/scratch.txt"}})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(proc.stdout, "")

    def test_out_of_repo_source_edit_is_ignored(self):
        # An absolute source path outside the project (e.g. a sibling repo edited in
        # the same session) must NOT queue drift here.
        with tempfile.TemporaryDirectory() as other:
            outside = os.path.join(other, "lib", "thing.py")
            proc = self.run_hook({"tool_input": {"file_path": outside}})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(proc.stdout, "")

    def test_dot_claude_edit_is_ignored(self):
        # .claude/ internals (hooks/config/skills) are not PRD-tracked implementation.
        proc = self.run_hook({"tool_input": {"file_path": ".claude/hooks/drift_queue.py"}})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(proc.stdout, "")

    def test_garbage_stdin_fails_open(self):
        proc = self.run_hook("this is not json")
        self.assertEqual(proc.returncode, 0)


class TestStopGate(TmpProject):
    def run_gate(self, payload=None):
        return subprocess.run(
            ["bash", STOP_GATE],
            input=json.dumps(payload or {}), capture_output=True, text=True,
            cwd=self.cwd)

    def _queue(self, *names):
        qdir = os.path.join(self.cwd, ".prd-drift-queue")
        os.makedirs(qdir, exist_ok=True)
        for n in names:
            open(os.path.join(qdir, n), "w").close()

    def test_empty_queue_allows_stop(self):
        proc = self.run_gate()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_nonempty_queue_blocks(self):
        self._queue("1_x.py", "prd_2_PRD.md")
        proc = self.run_gate()
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("2 change(s)", out["reason"])
        self.assertIn("1 PRD edit(s)", out["reason"])

    def test_stop_hook_active_never_double_blocks(self):
        self._queue("1_x.py")
        proc = self.run_gate({"stop_hook_active": True})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_bypass_marker_disables_gate(self):
        self._queue("1_x.py")
        open(os.path.join(self.cwd, ".no-drift-gate"), "w").close()
        proc = self.run_gate()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


class TestSessionRecallParsing(unittest.TestCase):
    def setUp(self):
        self.mod = _load_session_recall()
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def _write(self, text):
        path = os.path.join(self.dir.name, "CLAUDE.md")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_rendered_values_parse(self):
        path = self._write(CLAUDE_MD)
        cfg = self.mod.parse_claude_md(path)
        self.assertEqual(cfg["project_id"], "demo-api")
        self.assertEqual(cfg["project_type"], "web-api")
        self.assertEqual(cfg["prd_file"], "docs/PRD.md")

    def test_unrendered_placeholders_are_skipped(self):
        path = self._write("- PROJECT_ID: <unique-kebab-case-id>\n"
                           "- PRD_FILE: <relative path to your PRD, e.g. docs/PRD.md>\n")
        cfg = self.mod.parse_claude_md(path)
        self.assertNotIn("project_id", cfg)
        self.assertNotIn("prd_file", cfg)

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.mod.parse_claude_md(
            os.path.join(self.dir.name, "nope.md")), {})


class TestSessionRecallFailOpen(TmpProject):
    def run_hook(self, with_claude_md=True):
        if not with_claude_md:
            os.remove(os.path.join(self.cwd, "CLAUDE.md"))
        env = dict(os.environ)
        # Force an unreachable host so the hook cannot touch a real database,
        # and env-pin every setting so ~/.claude.json is never consulted.
        env.update({"ARANGO_HOSTS": "http://127.0.0.1:9",
                    "ARANGO_ROOT_USERNAME": "nobody",
                    "ARANGO_ROOT_PASSWORD": "x",
                    "ARANGO_DEFAULT_DB_NAME": "memory",
                    "ARANGO_VERIFY_SSL": "true"})
        return subprocess.run([sys.executable, SESSION_RECALL],
                              capture_output=True, text=True, cwd=self.cwd, env=env,
                              timeout=30)

    def test_no_claude_md_exits_silently(self):
        proc = self.run_hook(with_claude_md=False)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_unreachable_db_fails_open(self):
        proc = self.run_hook()
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
