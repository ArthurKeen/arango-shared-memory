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


class TestCaptureMiner(unittest.TestCase):
    """The Stop-hook miner should detect resolved failures for Bash AND MCP tools."""

    def _load(self):
        spec = importlib.util.spec_from_file_location(
            "capture_candidates", os.path.join(HOOKS, "capture_candidates.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _transcript(self, entries):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.jsonl")
        with open(p, "w") as fh:
            for obj in entries:
                fh.write(json.dumps(obj) + "\n")
        return p

    @staticmethod
    def _use(tid, name, command=""):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": {"command": command}}]}}

    @staticmethod
    def _result(tid, out, is_error=False):
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": out, "is_error": is_error}]}}

    def test_mines_bash_and_mcp_resolved_failures(self):
        cc = self._load()
        entries = [
            self._use("b1", "Bash", "cd x && pytest -q"),
            self._result("b1", "E   assert False\nFAILED", is_error=True),
            self._use("m1", "execute-aql-query"),
            self._result("m1", json.dumps({"result": {"error": "AQL: syntax error", "error_code": 1501}})),
            self._use("b2", "Bash", "pytest -q"),
            self._result("b2", "3 passed"),
            self._use("m2", "execute-aql-query"),
            self._result("m2", json.dumps({"result": {"rows": []}})),
        ]
        candidates, n = cc.mine(self._transcript(entries))
        summaries = " ".join(c["summary"] for c in candidates)
        self.assertEqual(n, 4)                       # 4 tool events recorded (Bash + MCP)
        self.assertIn("pytest", summaries)           # Bash resolved-failure
        self.assertIn("execute-aql-query", summaries)  # MCP resolved-failure

    def test_edit_write_churn_is_ignored(self):
        cc = self._load()
        entries = [
            self._use("e1", "Edit", ""),
            self._result("e1", "error: file not found", is_error=True),
            self._use("e2", "Edit", ""),
            self._result("e2", "ok"),
        ]
        candidates, n = cc.mine(self._transcript(entries))
        self.assertEqual(n, 0)          # Edit/Write are not captured
        self.assertEqual(candidates, [])


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

    def test_markdown_decorated_values_parse(self):
        """Humans format these docs: `id`, **id**, "id".

        A real project (arango-sparql-py) wrapped every identity value in
        backticks; the old [A-Za-z0-9._-]+ pattern could not match it, so
        project_id came back empty and the ENTIRE digest returned early — silent,
        indistinguishable from "nothing to report" (same failure mode as the
        reserved-keyword bug in PR #1).
        """
        path = self._write("- PROJECT_ID: `arango-sparql-py`\n"
                           "- PROJECT_TYPE: **microservice**\n"
                           '- PRD_FILE: "docs/architecture/PRD.md"\n')
        cfg = self.mod.parse_claude_md(path)
        self.assertEqual(cfg["project_id"], "arango-sparql-py")
        self.assertEqual(cfg["project_type"], "microservice")
        self.assertEqual(cfg["prd_file"], "docs/architecture/PRD.md")

    def test_decorated_placeholders_are_still_skipped(self):
        """Stripping decoration must not let a placeholder through."""
        path = self._write("- PROJECT_ID: `<unique-kebab-case-id>`\n")
        self.assertNotIn("project_id", self.mod.parse_claude_md(path))

    def test_agents_md_is_preferred_over_claude_md(self):
        # AGENTS.md (the consolidated canonical doc) wins when both exist; a stale
        # CLAUDE.md left over from the migration must not shadow it.
        with open(os.path.join(self.dir.name, "AGENTS.md"), "w") as fh:
            fh.write("- PROJECT_ID: from-agents\n- PRD_FILE: docs/PRD.md\n")
        with open(os.path.join(self.dir.name, "CLAUDE.md"), "w") as fh:
            fh.write("- PROJECT_ID: from-claude\n- PRD_FILE: STALE.md\n")
        cwd = os.getcwd()
        os.chdir(self.dir.name)  # no-arg parse resolves relative names against CWD
        try:
            cfg = self.mod.parse_claude_md()
        finally:
            os.chdir(cwd)
        self.assertEqual(cfg["project_id"], "from-agents")
        self.assertEqual(cfg["prd_file"], "docs/PRD.md")

    def test_falls_back_to_claude_md_when_no_agents_md(self):
        # Legacy repos that never migrated (only CLAUDE.md present) still resolve.
        with open(os.path.join(self.dir.name, "CLAUDE.md"), "w") as fh:
            fh.write(CLAUDE_MD)
        cwd = os.getcwd()
        os.chdir(self.dir.name)
        try:
            cfg = self.mod.parse_claude_md()
        finally:
            os.chdir(cwd)
        self.assertEqual(cfg["project_id"], "demo-api")
        self.assertEqual(cfg["prd_file"], "docs/PRD.md")


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
