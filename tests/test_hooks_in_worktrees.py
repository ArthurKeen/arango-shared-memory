"""The project-side hooks in a linked git worktree (stdlib unittest; needs git).

Claude Code runs hook commands in the session's cwd. A linked worktree has none of the
gitignored files the installers place in the main checkout (.claude/, usually CLAUDE.md,
.no-drift-gate), so commands naming `.claude/hooks/<x>` relative to the cwd failed on
every edit and every stop in every worktree session, and no drift was queued there
(2026-11-06). These tests install the hooks into a main checkout the way the installers
do, add a worktree, and run the settings.json commands themselves, as Claude Code does.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO, "templates", ".claude")
HOOK_PATHS = os.path.join(TEMPLATES, "hooks", "hook_paths.py")

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
CLAUDE_MD = "- PROJECT_ID: demo-api\n- PROJECT_TYPE: web-api\n- PRD_FILE: docs/PRD.md\n"
# What many projects commit: .claude/ and CLAUDE.md ignored, the queue directories not.
COMMITTED_GITIGNORE = ".claude/\nCLAUDE.md\n.no-drift-gate\n"


def _load_hook_paths():
    spec = importlib.util.spec_from_file_location("hook_paths", HOOK_PATHS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WorktreeProject(unittest.TestCase):
    """A main checkout with the hooks installed, and a linked worktree beside it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        # realpath: macOS's /var is a symlink, and git reports resolved paths.
        base = os.path.realpath(self.dir.name)
        self.main = os.path.join(base, "main")
        self.wt = os.path.join(base, "wt")
        self.home = os.path.join(base, "home")
        os.makedirs(os.path.join(self.main, "docs"))
        os.makedirs(self.home)
        self._write(self.main, ".gitignore", COMMITTED_GITIGNORE)
        self._write(self.main, "app.py", "x = 1\n")
        self._write(self.main, os.path.join("docs", "PRD.md"), "# PRD\n")
        self.git(self.main, "init", "-q")
        self.git(self.main, "add", "-A")
        self.git(self.main, "commit", "-qm", "init")
        shutil.copytree(os.path.join(TEMPLATES, "hooks"),
                        os.path.join(self.main, ".claude", "hooks"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(os.path.join(TEMPLATES, "settings.json"),
                    os.path.join(self.main, ".claude", "settings.json"))
        self._write(self.main, "CLAUDE.md", CLAUDE_MD)
        self.git(self.main, "worktree", "add", "-q", "-b", "feature", self.wt)
        with open(os.path.join(TEMPLATES, "settings.json"), encoding="utf-8") as fh:
            self.settings = json.load(fh)

    def tearDown(self):
        self.dir.cleanup()

    @staticmethod
    def _write(root, rel, text):
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def git(self, cwd, *args):
        return subprocess.run(GIT + list(args), cwd=cwd, check=True,
                              capture_output=True, text=True).stdout

    def command(self, script):
        """The settings.json command that runs .claude/hooks/<script>."""
        for groups in self.settings["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    if f".claude/hooks/{script}" in hook["command"]:
                        return hook["command"]
        self.fail(f"settings.json has no hook running {script}")

    def run_command(self, command, cwd, payload=None):
        # HOME and the ARANGO_* pins keep session_recall away from any real database.
        env = dict(os.environ, HOME=self.home, ARANGO_HOSTS="http://127.0.0.1:9",
                   ARANGO_ROOT_USERNAME="nobody", ARANGO_ROOT_PASSWORD="x",
                   ARANGO_DEFAULT_DB_NAME="memory", ARANGO_VERIFY_SSL="true")
        return subprocess.run(["bash", "-c", command], cwd=cwd, env=env, text=True,
                              input=json.dumps(payload or {}), capture_output=True,
                              timeout=60)

    def markers(self, root):
        qdir = os.path.join(root, ".prd-drift-queue")
        names = os.listdir(qdir) if os.path.isdir(qdir) else []
        return sorted(n for n in names if not n.startswith("."))

    def edit(self, root, rel):
        return self.run_command(self.command("drift_queue.py"), root,
                                {"tool_input": {"file_path": os.path.join(root, rel)}})


class TestCommandsFromAWorktree(WorktreeProject):
    def test_every_settings_command_finds_its_script(self):
        """The regression: each of these printed "can't open file" in a worktree."""
        commands = [hook["command"] for groups in self.settings["hooks"].values()
                    for group in groups for hook in group["hooks"]]
        self.assertEqual(len(commands), 5)
        for command in commands:
            with self.subTest(command=command):
                proc = self.run_command(command, self.wt, {"stop_hook_active": True})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("can't open file", proc.stderr)
                self.assertNotIn("No such file", proc.stderr)

    def test_an_edit_queues_drift_in_the_worktree_and_git_never_sees_it(self):
        proc = self.edit(self.wt, "app.py")
        self.assertIn("Implementation file modified: app.py", proc.stdout)
        self.assertEqual(len(self.markers(self.wt)), 1)
        self.assertEqual(self.markers(self.main), [])
        # The committed .gitignore does not list the queue; its own .gitignore hides it.
        self.assertEqual(self.git(self.wt, "status", "--porcelain", "--untracked-files=all"), "")

    def test_a_prd_edit_is_recognised_from_the_main_checkouts_claude_md(self):
        self.assertFalse(os.path.exists(os.path.join(self.wt, "CLAUDE.md")))
        self.edit(self.wt, os.path.join("docs", "PRD.md"))
        self.assertEqual([m.startswith("prd_") for m in self.markers(self.wt)], [True])

    def test_the_stop_gate_blocks_on_the_worktrees_queue(self):
        self.edit(self.wt, "app.py")
        proc = self.run_command(self.command("drift_stop_gate.sh"), self.wt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("1 change(s)", out["reason"])

    def test_the_stop_gate_reconciles_a_shell_edit_in_the_worktree(self):
        """The reconciler is a sibling script; it used to be run cwd-relative too."""
        self._write(self.wt, "app.py", "x = 2\n")  # no hook saw this edit
        proc = self.run_command(self.command("drift_stop_gate.sh"), self.wt)
        self.assertEqual(json.loads(proc.stdout)["decision"], "block")
        self.assertIn("Reconciled 1 change(s)", proc.stderr)

    def test_the_main_checkouts_bypass_covers_its_worktrees(self):
        self.edit(self.wt, "app.py")
        self._write(self.main, ".no-drift-gate", "")
        proc = self.run_command(self.command("drift_stop_gate.sh"), self.wt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_the_dismiss_command_in_the_block_message_runs_from_the_worktree(self):
        state_dir = os.path.join(self.wt, ".claude", ".shared-memory-sessions")
        os.makedirs(state_dir)
        self._write(state_dir, "s1.json", json.dumps({"surfaced_keys": ["p1"]}))
        gate = self.command("drift_stop_gate.sh")
        reason = json.loads(self.run_command(gate, self.wt, {"session_id": "s1"}).stdout)["reason"]
        dismiss = re.search(r"(python3 \S+dismiss_surfaced\.py s1 p1)", reason)
        self.assertIsNotNone(dismiss, reason)
        self.assertTrue(os.path.isabs(dismiss.group(1).split()[1]))
        proc = self.run_command(dismiss.group(1), self.wt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.run_command(gate, self.wt, {"session_id": "s1"}).stdout.strip(), "")

    def test_session_recall_reads_the_identity_from_the_main_checkout(self):
        hooks = os.path.join(self.main, ".claude", "hooks")
        code = (f"import json, sys; sys.path.insert(0, {hooks!r}); import session_recall; "
                "print(json.dumps(session_recall.parse_claude_md()))")
        proc = subprocess.run([sys.executable, "-c", code], cwd=self.wt,
                              capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proc.stdout), {"project_id": "demo-api",
                                                   "project_type": "web-api",
                                                   "prd_file": "docs/PRD.md"})


class TestCommandsFromTheMainCheckout(WorktreeProject):
    def test_the_main_checkout_runs_its_own_hooks_and_stays_clean_in_git(self):
        proc = self.edit(self.main, "app.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.markers(self.main)), 1)
        self.assertEqual(self.markers(self.wt), [])
        self.assertEqual(self.git(self.main, "status", "--porcelain", "--untracked-files=all"), "")


class TestHookPaths(unittest.TestCase):
    def setUp(self):
        self.mod = _load_hook_paths()
        self.dir = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.dir.name)

    def tearDown(self):
        os.chdir(self.previous)
        self.dir.cleanup()

    def test_outside_git_there_is_no_main_checkout_and_no_fallback(self):
        self.assertIsNone(self.mod.main_checkout())
        self.assertEqual(list(self.mod.agent_doc_paths()), ["AGENTS.md", "CLAUDE.md"])

    def test_ensure_private_dir_is_idempotent_and_keeps_an_existing_gitignore(self):
        self.mod.ensure_private_dir("q")
        with open(os.path.join("q", ".gitignore"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.mod.PRIVATE_DIR_GITIGNORE)
        with open(os.path.join("q", ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("custom\n")
        self.mod.ensure_private_dir("q")
        with open(os.path.join("q", ".gitignore"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "custom\n")


if __name__ == "__main__":
    unittest.main()
