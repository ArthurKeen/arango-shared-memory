"""Guard for issue #11: session state must not split across a git worktree.

The apply-gate state was addressed relative to the CURRENT DIRECTORY
(`.claude/.shared-memory-sessions/<session>.json`). When a session's cwd moved into a
linked git worktree, its state split across two files: surfaced keys in one, applied
keys in the other. The Stop gate then read whichever sat under the cwd at stop time:

  - from the main checkout, it saw surfaced keys whose pattern-applied calls had landed
    in the worktree, and KEPT BLOCKING after every key was resolved
  - from the worktree, it saw nothing surfaced and let the session END with keys
    unresolved

Hit in a real r2g session (2026-11-12), building a PR in a worktree under the session's
scratchpad. The only way out was merging the two files by hand: calling pattern-applied
again would have double-counted the reuse.

These tests run the reproduction from the issue and assert the acceptance criterion it
asks for -- the gate reports nothing pending from BOTH directories -- plus the migration
of state that is already split, and that the gate still blocks when a key genuinely is
unresolved (a fix that simply stopped blocking would pass a weaker test).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Fixture:
    """A repo with the hooks installed and a linked worktree beside it."""

    def __init__(self, tmp):
        self.repo = os.path.join(tmp, "repo")
        self.wt = os.path.join(tmp, "wt")
        os.makedirs(os.path.join(self.repo, ".claude", "hooks"))
        for name in os.listdir(HOOKS):
            if name.endswith((".py", ".sh")):
                shutil.copy(os.path.join(HOOKS, name),
                            os.path.join(self.repo, ".claude", "hooks", name))
        _git(["init", "-q"], self.repo)
        _git(["-c", "user.email=t@t", "-c", "user.name=t",
              "commit", "-q", "--allow-empty", "-m", "init"], self.repo)
        _git(["worktree", "add", "-q", self.wt], self.repo)

    @property
    def tracker(self):
        return os.path.join(self.repo, ".claude", "hooks", "pattern_apply_tracker.py")

    @property
    def gate(self):
        return os.path.join(self.repo, ".claude", "hooks", "drift_stop_gate.sh")

    def _env(self, project_dir=None):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = project_dir
        return env

    def track(self, cwd, payload, project_dir=None):
        subprocess.run(["python3", self.tracker], cwd=cwd, env=self._env(project_dir),
                       input=json.dumps(payload), text=True, capture_output=True,
                       timeout=30)

    def stop(self, cwd, session, project_dir=None):
        r = subprocess.run(["bash", self.gate], cwd=cwd, env=self._env(project_dir),
                           input=json.dumps({"session_id": session}), text=True,
                           capture_output=True, timeout=30)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return None

    def state_files(self, session):
        out = {}
        for label, base in (("repo", self.repo), ("wt", self.wt)):
            path = os.path.join(base, ".claude", ".shared-memory-sessions",
                                f"{session}.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    out[label] = json.load(fh)
        return out

    def write_state(self, base, session, data):
        d = os.path.join(base, ".claude", ".shared-memory-sessions")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{session}.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)


SEARCH = {"tool_name": "mcp__arangodb-memory-mcp__pattern-search",
          "tool_response": {"patterns": [{"_key": "k1"}]}}
APPLIED = {"tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
           "tool_input": {"keys": ["k1"], "outcome": "worked"}}


class TestSessionStateDoesNotSplitAcrossAWorktree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = _Fixture(self._tmp.name)

    def test_surfacing_and_applying_from_different_trees_share_one_file(self):
        """The reproduction from the issue, verbatim in effect."""
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))
        self.fx.track(self.fx.wt, dict(APPLIED, session_id="s1"))
        files = self.fx.state_files("s1")
        self.assertNotIn("wt", files,
                         f"state was written inside the worktree: {files}")
        self.assertIn("repo", files, "no state was written at all")
        self.assertEqual(sorted(files["repo"].get("surfaced_keys", [])), ["k1"])
        self.assertEqual(sorted(files["repo"].get("applied_keys", [])), ["k1"])

    def test_the_gate_reports_nothing_pending_from_either_directory(self):
        """The acceptance criterion the issue asks for."""
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))
        self.fx.track(self.fx.wt, dict(APPLIED, session_id="s1"))
        for label, cwd in (("main checkout", self.fx.repo), ("worktree", self.fx.wt)):
            with self.subTest(cwd=label):
                self.assertIsNone(
                    self.fx.stop(cwd, "s1"),
                    f"the gate still blocked from the {label} after every surfaced "
                    "key was applied")

    def test_state_already_split_is_merged_on_the_next_read(self):
        """Operators have split files on disk right now; the fix has to pick them up.

        Union, because the split is exactly that -- surfaced keys on one side, applied
        and dismissed on the other.
        """
        self.fx.write_state(self.fx.repo, "old", {"surfaced_keys": ["k1", "k2"]})
        self.fx.write_state(self.fx.wt, "old",
                            {"applied_keys": ["k1"], "dismissed_keys": ["k2"]})
        self.assertIsNone(self.fx.stop(self.fx.wt, "old"),
                          "the gate blocked despite both keys being resolved")
        merged = self.fx.state_files("old")["repo"]
        self.assertEqual(sorted(merged["surfaced_keys"]), ["k1", "k2"])
        self.assertEqual(sorted(merged["applied_keys"]), ["k1"])
        self.assertEqual(sorted(merged["dismissed_keys"]), ["k2"])

    def test_the_gate_still_blocks_on_a_genuinely_unresolved_key(self):
        """A 'fix' that merely stopped blocking would pass the tests above.

        This is the one that says the gate still does its job.
        """
        self.fx.write_state(self.fx.repo, "p",
                            {"surfaced_keys": ["a", "b"], "applied_keys": ["a"]})
        for label, cwd in (("main checkout", self.fx.repo), ("worktree", self.fx.wt)):
            with self.subTest(cwd=label):
                out = self.fx.stop(cwd, "p")
                self.assertIsNotNone(out, f"no block decision from the {label}")
                self.assertEqual(out["decision"], "block")
                self.assertIn("1 surfaced pattern(s) unresolved", out["reason"])

    def test_the_project_dir_overrides_both_trees(self):
        """CLAUDE_PROJECT_DIR is the most trustworthy signal and must win."""
        self.fx.track(self.fx.wt, dict(SEARCH, session_id="s2"),
                      project_dir=self.fx.repo)
        files = self.fx.state_files("s2")
        self.assertIn("repo", files)
        self.assertNotIn("wt", files)

    def test_state_is_kept_out_of_git(self):
        """A worktree only has the committed .gitignore, so the directory ignores
        itself -- otherwise the fix would make every session dirty the checkout."""
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s3"))
        r = subprocess.run(["git", "status", "--porcelain"], cwd=self.fx.repo,
                           capture_output=True, text=True, check=True)
        self.assertNotIn("shared-memory-sessions", r.stdout,
                         f"session state shows as untracked: {r.stdout!r}")


class TestOneDefinitionOfTheStateDirectory(unittest.TestCase):
    """Five places addressed the directory independently; that is how the writers and
    the gate came to disagree. They must all go through hook_paths."""

    def test_no_hook_builds_the_path_itself(self):
        offenders = []
        for rel in (os.path.join("templates", ".claude", "hooks",
                                 "pattern_apply_tracker.py"),
                    os.path.join("templates", ".claude", "hooks",
                                 "dismiss_surfaced.py")):
            with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
                src = fh.read()
            for line in src.splitlines():
                if ".shared-memory-sessions" in line and "state_dir" not in line \
                        and not line.lstrip().startswith("#"):
                    offenders.append(f"{rel}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "a hook still builds the state path itself instead of "
                         f"calling hook_paths.state_dir(): {offenders}")

    def test_the_gate_imports_the_shared_resolver(self):
        with open(os.path.join(HOOKS, "drift_stop_gate.sh"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("from hook_paths import", src,
                      "the gate resolves the state directory on its own")
        self.assertIn('sys.argv[1]', src,
                      "the gate derives the hooks directory from __file__, which does "
                      "not exist under `python3 -c` — the fallback would always win")


if __name__ == "__main__":
    unittest.main()
