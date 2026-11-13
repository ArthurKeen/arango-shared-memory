"""Guards for issues #12 and #13: hook dispatch outside a repo, and harness text.

#12 -- every hook command in templates/.claude/settings.json resolved its script
relative to the CURRENT DIRECTORY, falling back to the main checkout via
`git worktree list`. Outside a git repository that prints nothing, so the path became
"/.claude/hooks/<script>", python3 exited 2, and Claude Code treats exit 2 from a
PostToolUse hook as a BLOCKING error shown to the model. The hooks are written to fail
open; this wrapper failed before they ran. It fired three times in one r2g session
(2026-11-13) whenever the shell had moved into a scratch folder -- and one of those was
a pattern-applied call, so the apply tracker never recorded the reuse.

#13 -- capture_candidates.py mined user-role text for corrections, skipping four
harness prefixes. It did not skip hook feedback, which Claude Code also delivers in a
user-role message. So the apply gate's own block message came back as a candidate
correction FROM THE USER: triage of an r2g queue on 2026-11-13 found the only
user-correction among 22 candidates was the gate text, matched on "still failing" from
the sentence describing a failed pattern application.

Stdlib-only and source-level like the other guards here: CI installs no dependencies.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, "templates", ".claude", "settings.json")
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")


def _commands():
    with open(SETTINGS, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = []
    for event, groups in (doc.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks") or []:
                out.append((event, h["command"]))
    return out


class TestHookCommandsNeverBlockASession(unittest.TestCase):
    def setUp(self):
        self.commands = _commands()
        self.assertGreaterEqual(len(self.commands), 5,
                                "settings.json hook scan found almost nothing — this "
                                "guard would pass vacuously")

    def _run(self, command, cwd, project_dir=""):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project_dir)
        return subprocess.run(["sh", "-c", command], cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=30)

    def test_every_command_exits_zero_outside_a_git_repository(self):
        """The reported bug. Exit 2 from a PostToolUse hook is a blocking error."""
        with tempfile.TemporaryDirectory() as d:
            for event, cmd in self.commands:
                with self.subTest(event=event):
                    r = self._run(cmd, d)
                    self.assertEqual(
                        r.returncode, 0,
                        f"{event} hook exited {r.returncode} outside a git repo — "
                        f"Claude Code shows that to the model as a blocking error. "
                        f"stderr: {r.stderr[:200]}")
                    self.assertNotIn("can't open file", r.stderr)

    def test_every_command_exits_zero_in_a_repo_with_no_hooks_installed(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            for event, cmd in self.commands:
                with self.subTest(event=event):
                    r = self._run(cmd, d)
                    self.assertEqual(r.returncode, 0,
                                     f"{event} hook exited {r.returncode}: "
                                     f"{r.stderr[:200]}")

    def test_the_project_dir_is_used_when_the_cwd_has_no_hooks(self):
        """CLAUDE_PROJECT_DIR is what makes the hook RUN rather than merely not fail.

        Without it, a session whose shell has moved outside the project silently skips
        every hook -- which is how a pattern-applied call went unrecorded.
        """
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "proj")
            os.makedirs(os.path.join(proj, ".claude", "hooks"))
            elsewhere = os.path.join(d, "elsewhere")
            os.makedirs(elsewhere)
            for event, cmd in self.commands:
                script = cmd.split("f=.claude/hooks/", 1)[1].split(";", 1)[0]
                path = os.path.join(proj, ".claude", "hooks", script)
                runner = "python3" if script.endswith(".py") else "bash"
                body = ("import sys; print('RAN')\n" if runner == "python3"
                        else "echo RAN\n")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                r = self._run(cmd, elsewhere, project_dir=proj)
                with self.subTest(event=event, script=script):
                    self.assertEqual(r.returncode, 0, r.stderr[:200])
                    self.assertIn("RAN", r.stdout,
                                  f"{event} hook did not run from CLAUDE_PROJECT_DIR")

    def test_no_command_can_resolve_to_an_absolute_root_path(self):
        """The precise defect: an empty fallback made the path "/.claude/hooks/...".

        Guarded at the source, because a future edit could reintroduce the bare
        "$(...)/$f" concatenation without any test directory reproducing it.
        """
        for event, cmd in self.commands:
            with self.subTest(event=event):
                self.assertNotIn('")/$f"', cmd,
                                 "an empty command substitution would make this an "
                                 "absolute path at the filesystem root")
                self.assertIn('[ -n "$d" ]', cmd,
                              "the candidate directory is not checked for emptiness")
                self.assertIn("exit 0", cmd,
                              "a missing hook script does not exit 0, so it would "
                              "block the session")

    def test_the_project_dir_is_tried_before_the_cwd(self):
        for event, cmd in self.commands:
            with self.subTest(event=event):
                self.assertLess(
                    cmd.index("$CLAUDE_PROJECT_DIR"), cmd.index("git worktree list"),
                    "the git fallback is consulted before CLAUDE_PROJECT_DIR")


def _miner():
    spec = importlib.util.spec_from_file_location(
        "capture_candidates", os.path.join(HOOKS, "capture_candidates.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestTheMinerDoesNotMineItsOwnGate(unittest.TestCase):
    """#13: the apply gate's block message was filed as a user correction."""

    def setUp(self):
        self.m = _miner()

    def _mine(self, records):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r) + "\n")
            return self.m.mine(path)

    GATE = ("Stop hook feedback: [SHARED-MEMORY APPLY GATE] 8 surfaced pattern(s) "
            "unresolved. THREE outcomes, not two. (1) It informed the solution: "
            "pattern-applied with only those key(s), outcome='worked'. (2) You TRIED "
            "it and it did not help — the checks it should have made pass are still "
            "failing, you reverted it for another approach, or it introduced a "
            "defect: pattern-applied with outcome='failed'.")

    def test_the_gate_text_would_match_the_correction_pattern(self):
        """Guard the guard: if it stopped matching, this test would pass for the
        wrong reason and the skip rule could be deleted unnoticed."""
        self.assertTrue(self.m.CORRECTION_RE.search(self.GATE[:400]),
                        "the gate text no longer trips CORRECTION_RE, so this test no "
                        "longer proves the skip rule does anything")

    def test_stop_hook_feedback_is_not_a_user_correction(self):
        cands, _ = self._mine([{"type": "user",
                                "message": {"content": self.GATE}}])
        kinds = [c["kind"] for c in cands]
        self.assertNotIn("user-correction", kinds,
                         f"the gate's own message was mined as a correction: {cands}")

    def test_a_real_correction_is_still_captured(self):
        """The skip must not be so broad that genuine feedback stops being mined."""
        cands, _ = self._mine([{
            "type": "user", "promptSource": "sdk",
            "message": {"content": "no, that's wrong — you should have used the "
                                   "existing helper instead of writing a new one"}}])
        self.assertIn("user-correction", [c["kind"] for c in cands],
                      "a genuine typed correction is no longer captured")

    def test_other_harness_channels_are_skipped_too(self):
        for prefix in ("<system-reminder", "[SYSTEM", "PostToolUse:",
                       "This session is being continued from"):
            with self.subTest(prefix=prefix):
                cands, _ = self._mine([{"type": "user", "message": {
                    "content": prefix + " ... that's wrong, it is still failing"}}])
                self.assertNotIn("user-correction", [c["kind"] for c in cands])

    def test_structural_markers_exclude_but_never_include(self):
        """promptSource/isMeta may be absent on older transcripts. They must only ever
        REMOVE a candidate -- gating capture on them would silently stop all
        correction mining instead of producing one false positive."""
        typed = {"type": "user", "message": {"content": "no, that's wrong"}}
        self.assertIn("user-correction",
                      [c["kind"] for c in self._mine([typed])[0]],
                      "a record with no promptSource was dropped — the structural "
                      "signal is being used to include rather than exclude")
        for marker in ({"isMeta": True}, {"promptSource": "system"}):
            with self.subTest(marker=marker):
                rec = dict(typed, **marker)
                self.assertNotIn("user-correction",
                                 [c["kind"] for c in self._mine([rec])[0]])


if __name__ == "__main__":
    unittest.main()
