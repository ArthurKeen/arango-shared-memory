"""An apply outcome has to survive contact with the project's own checks.

Every outcome this system has ever stored is 'worked': 288 of 288, across 91 patterns,
0 failures. The search ranking multiplies relevance by (0.6 + 0.4*success_rate), and
with no failure ever recorded that term is a constant 1.4 for all 223 patterns -- it
cancels out of every comparison, so a ranking signal the system pays to compute ranks
nothing. Two rounds of documenting the 'failed' option (round 8 shipped a closed value
set, round 9 put all three doors in the gate message) moved the number by zero, which
is the evidence that documentation is the wrong instrument: an outcome an agent merely
asserts costs nothing to assert.

So these guards cover the mechanism that stops relying on the assertion. The hook
records whether the project's own checks were passing, and a 'worked' claim made while
the last check was red is CONTESTED -- clearable by re-running the check and getting
green (an action), or by recording the failure (the truth), but not by restating the
claim.

What is deliberately NOT tested here, because it is deliberately not built: inferring
an apply event from a search result. docs/implementation-plan.md excludes that, and
rightly -- a viewed result is not a reused one. Only the SIGN of a claim the agent has
already made is checked here, against evidence the agent did not author.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_session_state_location import _Fixture  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")
TRACKER = os.path.join(HOOKS, "pattern_apply_tracker.py")


def _load_tracker():
    import importlib.util
    spec = importlib.util.spec_from_file_location("apply_tracker_under_test", TRACKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_TRACKER = _load_tracker()

SEARCH = {"tool_name": "mcp__arangodb-memory-mcp__pattern-search",
          "tool_response": {"patterns": [{"_key": "k1"}]}}


def applied(outcome="worked", keys=("k1",)):
    return {"tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
            "tool_input": {"keys": list(keys), "outcome": outcome}}


def bash(command, ok=True, code=1, interrupted=False, background=False,
         stdout="ok", text=None):
    """A PostToolUse Bash payload in the two shapes Claude Code actually produces.

    Confirmed against stored transcripts, not assumed: a command that SUCCEEDS comes
    back as a dict with stdout/stderr, and one that FAILS has its result replaced by
    the string "Error: Exit code N ...". That difference is structural, which is why
    the hook reads it instead of guessing from the words in stdout -- a passing run
    that prints "FAILED" in a summary line would fool any text heuristic.
    """
    if ok:
        response = {"stdout": stdout, "stderr": "", "interrupted": interrupted,
                    "isImage": False, "noOutputExpected": False}
        if background:
            response["backgroundTaskId"] = "bg-1"
    else:
        response = text if text is not None else (
            f"Error: Exit code {code}\nsome failing output")
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": response}


class EvidenceFixture(_Fixture):
    def state(self, session="s1"):
        return self.state_files(session).get("repo", {})

    def verifications(self, session="s1"):
        return self.state(session).get("verifications", [])


class TestVerificationLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def test_a_failing_check_is_recorded_red(self):
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        entries = self.fx.verifications()
        self.assertEqual(len(entries), 1, f"nothing recorded: {self.fx.state()}")
        self.assertFalse(entries[-1]["ok"])
        self.assertEqual(entries[-1]["exit"], 1)
        self.assertEqual(entries[-1]["cmd"], "pytest -q")

    def test_a_passing_check_is_recorded_green(self):
        self.fx.track(self.fx.repo, dict(bash("python3 -m pytest"), session_id="s1"))
        self.assertTrue(self.fx.verifications()[-1]["ok"])

    def test_a_lookup_that_merely_mentions_a_runner_is_not_a_verdict(self):
        """`grep -rn "pytest" .` exits 1 when it finds nothing. That is not a red test
        suite, and reading it as one would contest honest claims at random."""
        self.fx.track(self.fx.repo,
                      dict(bash('grep -rn "pytest" .', ok=False), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_an_interrupted_command_is_not_a_verdict(self):
        """Killed by the user or a timeout. No information either way, so no entry."""
        self.fx.track(self.fx.repo,
                      dict(bash("pytest", interrupted=True), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_runner_prefixes_are_seen(self):
        for cmd in ("uv run pytest -q", "npm run test", "cargo test",
                    "make check", "ruff check .", "npx tsc --noEmit"):
            with self.subTest(cmd=cmd):
                self.fx.track(self.fx.repo, dict(bash(cmd), session_id="s1"))
                self.assertEqual(self.fx.verifications()[-1]["cmd"], cmd)

    def test_an_ordinary_bash_call_writes_nothing(self):
        """This hook runs on EVERY Bash call in every bootstrapped project. Anything
        that is not a check must cost a regex and nothing else -- no state file, no
        git subprocess, no write."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fresh = EvidenceFixture(tmp.name)
        fresh.track(fresh.repo, dict(bash("git status --short"), session_id="never"))
        self.assertEqual(fresh.state_files("never"), {})

    def test_a_check_run_before_the_first_search_is_still_recorded(self):
        """Requiring a prior pattern-search dropped every check that ran BEFORE one,
        which is the ordinary order: reproduce the failure, search for a pattern, apply
        it, claim it worked. The red run came first, so nothing was ever contested and
        the mechanism was inert in the most common workflow it has."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fresh = EvidenceFixture(tmp.name)
        fresh.track(fresh.repo, dict(
            bash("python3 -m pytest", stdout="2 failed, 1 passed"), session_id="pre"))
        fresh.track(fresh.repo, dict(SEARCH, session_id="pre"))
        fresh.track(fresh.repo, dict(applied("worked"), session_id="pre"))
        self.assertIsNotNone(
            fresh.state_files("pre")["repo"].get("contested"),
            "a failure reproduced before the search left the claim uncontested")


class TestContestedClaims(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def test_worked_claimed_while_the_last_check_is_red_is_contested(self):
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        contested = self.fx.state().get("contested")
        self.assertIsNotNone(contested, "a success was accepted over a red check")
        self.assertEqual(contested["keys"], ["k1"])
        self.assertEqual(contested["cmd"], "pytest -q")

    def test_worked_claimed_while_the_checks_are_green_stands(self):
        self.fx.track(self.fx.repo, dict(bash("pytest -q"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))

    def test_rerunning_the_check_green_clears_the_contest(self):
        """The resolution that makes this mechanism worth having: the way to claim a
        success becomes producing a green check, which is an action, rather than
        repeating the word 'worked', which is not."""
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.fx.track(self.fx.repo, dict(bash("pytest -q"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"),
                          "a green re-run did not supersede the stale red evidence")

    def test_recording_the_failure_clears_the_contest(self):
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("failed"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))

    def test_an_omitted_outcome_is_still_treated_as_the_claim_it_becomes(self):
        """The server's `outcome` default was removed on 2026-11-14, but an old client
        can still omit it. Whatever reaches the database, the hook must not let an
        unstated outcome escape the check that a stated one gets."""
        payload = {"tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
                   "tool_input": {"keys": ["k1"]}, "session_id": "s1"}
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, payload)
        self.assertIsNotNone(self.fx.state().get("contested"))


class TestGateBlocksOnContestedClaims(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def _contest(self):
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest tests/", ok=False, code=2),
                           session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))

    def test_a_contested_claim_blocks_with_nothing_else_outstanding(self):
        """Every surfaced key is applied and the drift queue is empty, so the gate's
        other two reasons to block are both satisfied. The contest alone must still
        stop the session, or the mechanism is advisory."""
        self._contest()
        out = self.fx.stop(self.fx.repo, "s1")
        self.assertIsNotNone(out, "the gate let a contested success end the session")
        self.assertEqual(out["decision"], "block")

    def test_the_block_names_the_evidence_and_both_resolutions(self):
        self._contest()
        reason = self.fx.stop(self.fx.repo, "s1")["reason"]
        self.assertIn("python3 -m pytest tests/", reason)
        self.assertIn("exited 2", reason)
        self.assertIn("re-run that check", reason)
        self.assertIn("outcome='failed'", reason)
        self.assertIn("k1", reason)

    def test_a_cleared_contest_lets_the_session_end(self):
        self._contest()
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest tests/"), session_id="s1"))
        self.assertIsNone(self.fx.stop(self.fx.repo, "s1"),
                          "the gate still blocked after the check went green")

    def test_the_loop_breaker_still_wins(self):
        """A contested claim must never be able to trap a session: at most one block
        per stop chain, like every other reason this gate has."""
        self._contest()
        r = subprocess.run(["bash", self.fx.gate], cwd=self.fx.repo,
                           env=self.fx._env(),
                           input=json.dumps({"session_id": "s1",
                                             "stop_hook_active": True}),
                           text=True, capture_output=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "")


class TestTheGuardsFailWhenTheMechanismIsRemoved(unittest.TestCase):
    """Mutation test. Four guards written in this repo turned out to be inert -- one
    asserted something unrelated code already satisfied, one named a test that matched
    nothing and still reported 'caught', one mutated a string that did not exist. So a
    guard here is only trusted once the suite has been watched to FAIL without the code
    it claims to protect, and the mutation is proved to have landed.
    """

    def test_removing_the_contest_check_breaks_the_suite(self):
        with open(TRACKER, encoding="utf-8") as fh:
            source = fh.read()
        target = "if not claimed or not reds:"
        self.assertIn(target, source, "mutation target vanished; this test is inert")
        mutated = source.replace(target, "if True:")
        self.assertNotEqual(mutated, source, "the mutation did not apply")

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        hooks = os.path.join(tmp, "hooks")
        shutil.copytree(HOOKS, hooks, ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(hooks, "pattern_apply_tracker.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(mutated)

        fx_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fx_dir, True)
        fx = EvidenceFixture(fx_dir)
        shutil.copy(os.path.join(hooks, "pattern_apply_tracker.py"),
                    os.path.join(fx.repo, ".claude", "hooks",
                                 "pattern_apply_tracker.py"))
        fx.track(fx.repo, dict(SEARCH, session_id="m1"))
        fx.track(fx.repo, dict(bash("pytest -q", ok=False), session_id="m1"))
        fx.track(fx.repo, dict(applied("worked"), session_id="m1"))
        self.assertIsNone(
            fx.state_files("m1").get("repo", {}).get("contested"),
            "the mutation did not disable the check, so the guards above prove nothing")


class TestWhatTheShellActuallyReports(unittest.TestCase):
    """Findings from the review of this change. Each one was reproduced end to end
    before being fixed, and each is pinned here because every one of them would have
    left the mechanism reporting success while measuring nothing -- or, worse,
    contesting honest claims, which is the failure mode that actively pushes an agent
    toward recording a fabricated 'failed'."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def test_a_piped_test_run_is_not_read_as_passing(self):
        """`pytest -q 2>&1 | tail -20` exits 0 when tail does, however many tests
        failed. Piping test output through tail or head is the dominant form in real
        transcripts, so reading the pipeline's status as the suite's would have made
        false greens the common case -- and a false green does not merely miss a
        contest, it CLEARS one."""
        self.fx.track(self.fx.repo,
                      dict(bash("pytest -q 2>&1 | tail -20"), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_a_piped_green_cannot_clear_a_contest(self):
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.fx.track(self.fx.repo,
                      dict(bash("pytest -q 2>&1 | tail -20"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_redirecting_output_to_a_file_still_counts(self):
        """The thing one does INSTEAD of piping through tail. Masking the `&` in `2>&1`
        is what keeps this from being cut at the redirect."""
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest > /tmp/s.out 2>&1", ok=False),
                           session_id="s1"))
        self.assertEqual(len(self.fx.verifications()), 1)
        self.assertFalse(self.fx.verifications()[-1]["ok"])

    def test_a_pipe_inside_a_quoted_regex_is_not_a_pipeline(self):
        """`grep -nE "ruff|mypy|pytest" ci.yml` is a lookup. Reading the `|mypy` as a
        pipeline segment made grep-exits-1-on-no-match look like a red type check, so
        an honest success was contested and the session blocked naming a grep as the
        project's failing suite."""
        # The output deliberately contains a red summary marker. Without quote
        # masking the `|mypy` reads as a pipeline segment, the grep is taken for a type
        # check, and the line it printed is then read as that check's verdict. An
        # earlier version of this guard asserted only that nothing was recorded, which
        # stayed true via a different code path and made the test inert.
        self.fx.track(self.fx.repo,
                      dict(bash('grep -nE "ruff|mypy|pytest" ci.yml', ok=False,
                                text="Error: Exit code 1\nci.yml:12: 3 failed"),
                           session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])
        self.assertIsNone(self.fx.state().get("contested"),
                          "an honest claim was contested by a grep")

    def test_a_red_check_arriving_after_the_claim_still_contests_it(self):
        """The ordering templates/CLAUDE.md actually prescribes: record the reuse
        immediately, then keep working. Computing the contest only when pattern-applied
        fires left the mechanism inert in the project's own mandated workflow."""
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        contested = self.fx.state().get("contested")
        self.assertIsNotNone(contested, "a red check after the claim did not contest it")
        self.assertEqual(contested["keys"], ["k1"])
        self.assertEqual(self.fx.stop(self.fx.repo, "s1")["decision"], "block")

    def test_a_green_run_of_a_different_check_does_not_clear_the_contest(self):
        """The block says "re-run that check". Any-green-clears meant `ruff check .`
        resolved a contest raised over a still-red pytest."""
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(bash("ruff check ."), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.fx.track(self.fx.repo, dict(bash("pytest -q"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))

    def test_a_backgrounded_run_is_not_a_verdict(self):
        """It returns the moment it is launched; the suite has not finished."""
        self.fx.track(self.fx.repo,
                      dict(bash("pytest -q", background=True), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_recording_a_failure_for_another_key_leaves_the_contest_standing(self):
        self.fx.track(self.fx.repo,
                      dict(SEARCH, session_id="s1",
                           tool_response={"patterns": [{"_key": "k2"}]}))
        self.fx.track(self.fx.repo, dict(bash("pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked", keys=("k1",)), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("failed", keys=("k2",)), session_id="s1"))
        contested = self.fx.state().get("contested")
        self.assertIsNotNone(contested, "a failure for k2 cleared the contest about k1")
        self.assertEqual(contested["keys"], ["k1"])


class TestItActuallyFiresOnRealCommands(unittest.TestCase):
    """Second review round. The headline finding was that the detector was inert.

    Requiring the check to own the shell's exit status recognised 10 of the 13,299
    runner-invoking commands in 69,167 real Bash calls -- 0.1%. Real agent commands are
    whole shell programs: `cd X && ... <<PY ... PY`, `for ... done`, `S=$(mktemp)`
    preambles, output piped to tail. Shipping that would have put "mechanism deployed"
    in the scorecard while it provably never fired, and round 10 could not have told
    that apart from "the incentive argument was wrong".

    So the verdict now comes from what the runner SAYS -- its own summary line, which
    survives pipes, loops and heredocs -- and falls back to the exit status only where
    that status is the check's own. Replayed over the same corpus: 75.5% of
    runner-invoking commands classify, 2,297 of them RED.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def test_a_piped_failing_suite_is_now_caught_by_its_own_summary(self):
        """The exact form that defeated the first attempt. Exit status is tail's (0),
        but pytest still said what happened."""
        self.fx.track(self.fx.repo, dict(
            bash("cd pkg && python3 -m pytest -q 2>&1 | tail -20",
                 stdout="3 failed, 12 passed in 2.14s"), session_id="s1"))
        entries = self.fx.verifications()
        self.assertEqual(len(entries), 1, "a piped failing suite still went unseen")
        self.assertFalse(entries[-1]["ok"])

    def test_a_heredoc_script_that_runs_tests_is_classified(self):
        self.fx.track(self.fx.repo, dict(
            bash("S=$(mktemp -d); cd $S && python3 -m unittest discover tests "
                 "> /tmp/o 2>&1; cat /tmp/o",
                 stdout="Ran 211 tests in 13.8s\n\nFAILED (failures=2)"),
            session_id="s1"))
        self.assertFalse(self.fx.verifications()[-1]["ok"])

    def test_a_green_summary_is_read_as_green(self):
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m unittest discover tests 2>&1 | tail -3",
                 stdout="Ran 211 tests in 13.8s\n\nOK (skipped=2)"), session_id="s1"))
        self.assertTrue(self.fx.verifications()[-1]["ok"])

    def test_a_timeout_is_not_a_failing_suite(self):
        """It arrives as "Error: Exit code 143 / Command timed out after 2m 0s", and
        the dict-shape `interrupted` flag never accompanies it. Read as a clean RED, a
        suite that merely ran long would contest an honest claim."""
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest", ok=False,
                 text="Error: Exit code 143\nCommand timed out after 2m 0s"),
            session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_ruff_format_is_not_a_verdict_on_the_code(self):
        """`ruff format` rewrites files; `--check` exits 1 when a file merely WOULD be
        reformatted. Neither says the code is wrong."""
        self.fx.track(self.fx.repo,
                      dict(bash("ruff format --check .", ok=False), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])
        self.fx.track(self.fx.repo,
                      dict(bash("ruff check .", ok=False), session_id="s1"))
        self.assertEqual(len(self.fx.verifications()), 1)

    def test_zero_failures_is_not_a_failure(self):
        self.fx.track(self.fx.repo,
                      dict(bash("pytest -q", stdout="0 failed, 9 passed"),
                           session_id="s1"))
        self.assertTrue(self.fx.verifications()[-1]["ok"])

    def test_the_word_failed_in_passing_output_is_not_a_summary_line(self):
        """The whole reason detection is not a grep for "error" or "FAILED": a passing
        run that prints the word in a diff, a docstring or a test NAME must stay green."""
        self.fx.track(self.fx.repo, dict(
            bash("pytest -q",
                 stdout="test_reports_FAILED_banner PASSED\n9 passed in 0.3s"),
            session_id="s1"))
        self.assertTrue(self.fx.verifications()[-1]["ok"])

    def test_a_long_command_can_still_be_cleared_by_re_running_it(self):
        """The contest stored cmd[:200] while clearing compared the full command, so
        any check whose line exceeded 200 chars could never be resolved by re-running
        it -- leaving a false `failed` as the only way out of the gate."""
        long_cmd = ("cd pkg && python3 -m pytest " + "-k not_slow " * 20).strip()
        self.assertGreater(len(long_cmd), 200)
        self.fx.track(self.fx.repo,
                      dict(bash(long_cmd, stdout="2 failed, 1 passed"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.fx.track(self.fx.repo,
                      dict(bash(long_cmd, stdout="3 passed"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"),
                          "re-running the exact same check did not clear the contest")

    def test_bashoutput_polls_do_not_take_the_slow_path(self):
        """`Bash` as an unanchored regex alternative also selects `BashOutput`, which
        polls a background task. settings.json is anchored now, but installed projects
        carry their own copy, so the hook refuses the name too."""
        # Comparing state CONTENT proves nothing here: the slow path loads the file
        # and writes it back unchanged, so an inert version of this test passed with
        # the guard removed. The observable is the write itself -- _save swaps in a new
        # file via os.replace, so taking the slow path changes the inode.
        path = os.path.join(self.fx.repo, ".claude", ".shared-memory-sessions",
                            "s1.json")
        before = os.stat(path)
        self.fx.track(self.fx.repo, {
            "tool_name": "BashOutput", "session_id": "s1",
            "tool_input": {"bash_id": "bg-1"},
            "tool_response": {"stdout": "9 failed", "stderr": ""}})
        after = os.stat(path)
        self.assertEqual(before.st_ino, after.st_ino,
                         "BashOutput took the slow path and rewrote the state file")


class TestWhatTheRunnerSaidVersusWhatTheShellReturned(unittest.TestCase):
    """Third review round. Three of its five findings could each silently defeat the
    central guarantee -- a red check contesting an asserted success -- and two of them
    were earlier fixes coming back through a different door."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def _contest_with(self, cmd, out):
        self.fx.track(self.fx.repo, dict(bash(cmd, stdout=out), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))

    def test_a_different_piped_check_does_not_clear_the_contest(self):
        """Comparing contests by status segment meant the segment of ANY piped command
        was the pager, so a green `ruff check . | tail -20` cleared a contest raised by
        a red `pytest | tail -20` -- both being `tail -20`. The any-green-clears bug,
        returning for exactly the piping form this module calls dominant."""
        self._contest_with("python3 -m pytest -q 2>&1 | tail -20", "2 failed, 8 passed")
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.fx.track(self.fx.repo, dict(
            bash("ruff check . 2>&1 | tail -20", stdout="All checks passed!"),
            session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"),
                             "a different check cleared the contest")
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest -q 2>&1 | tail -20", stdout="10 passed"),
            session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))

    def test_an_early_green_stage_does_not_make_a_failing_script_green(self):
        """A compound command prints every stage. `make check` emitting unittest's OK
        before shellcheck fails would be recorded GREEN -- and re-running that same
        still-failing command would then CLEAR a real contest, which is worse than
        recording nothing at all."""
        self._contest_with("make check", "shellcheck: 2 issues\n1 failed")
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.fx.track(self.fx.repo, dict(
            bash("make check", ok=False,
                 text="Error: Exit code 1\nRan 9 tests\n\nOK\nshellcheck: 2 issues"),
            session_id="s1"))
        self.assertIsNotNone(
            self.fx.state().get("contested"),
            "re-running the same STILL-FAILING command was read as green, because an "
            "earlier stage printed OK, and it cleared a real contest")

    def test_a_runner_reporting_failure_is_believed_over_a_zero_exit(self):
        """The converse, and the case the mechanism exists for: the pipe hid it."""
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest 2>&1 | tail -5", stdout="4 failed, 2 passed"),
            session_id="s1"))
        self.assertFalse(self.fx.verifications()[-1]["ok"])

    def test_a_runner_named_as_an_argument_is_not_a_check(self):
        """`grep -rn pytest .` exits 1 on no match and would be stored as a RED project
        check, contesting an honest claim; `cat pytest.ini` exits 0 and would be stored
        GREEN, suppressing a legitimate one. Only the QUOTED form was guarded before."""
        cases = (
            ("grep -rn pytest .", False, "Error: Exit code 1\nnotes.md:4: 3 failed"),
            ("cat pytest.ini", True, "# see the run where 2 failed\n"),
            ("grep pytest pyproject.toml", True, "12 passed baseline\n"),
            ("ls | grep pytest", True, "pytest.ini\n9 passed\n"),
        )
        for cmd, ok, out in cases:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    _TRACKER.is_verification(cmd),
                    f"{cmd!r} was taken for a project check")
                self.fx.track(self.fx.repo, dict(
                    bash(cmd, ok=ok, stdout=out, text=out), session_id="s1"))
        self.assertEqual(self.fx.verifications(), [])

    def test_an_output_classified_red_never_says_exited_none(self):
        """It has no exit code of its own: the runner reported the failure and the
        shell returned the pager's 0."""
        self._contest_with("python3 -m pytest -q | tail -5", "7 failed, 1 passed")
        reason = self.fx.stop(self.fx.repo, "s1")["reason"]
        self.assertNotIn("exited None", reason)
        self.assertIn("reported failures", reason)

    def test_a_legacy_state_file_does_not_break_the_ledger(self):
        """`verifications` is a list of DICTS and merge_legacy_state unioned every list
        with set(), raising TypeError: unhashable type -- which the caller's
        `except (OSError, ValueError)` does not catch. In the worktree case this
        function exists for, the tracker would silently stop recording anything."""
        # Build a ledger first, then leave legacy state in the worktree, then fire an
        # event that goes through _path() -- the Bash fast path deliberately skips the
        # merge, so it is pattern-applied that trips over it.
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest", stdout="2 failed, 1 passed"), session_id="s1"))
        self.assertTrue(self.fx.verifications(), "no ledger to lose")
        self.fx.write_state(self.fx.wt, "s1", {
            "surfaced_keys": ["k9"],
            "verifications": [{"at": "2026-11-14T00:00:00Z", "cmd": "pytest -q",
                               "ok": False, "exit": 1}],
        })
        self.fx.track(self.fx.wt, dict(applied("worked"), session_id="s1"))
        state = self.fx.state()
        self.assertIn("k9", state.get("surfaced_keys", []),
                      "the legacy merge raised and was swallowed")
        self.assertTrue(state.get("verifications"),
                        "the ledger was lost when legacy state was merged")
        self.assertIsNotNone(state.get("contested"),
                             "the contest did not survive the merge")


class TestTheContestIsDerivedNotPatched(unittest.TestCase):
    """Fourth review round. Every bug in this area had the same shape: the contest was
    computed in two places under two slightly different rules, and each fix closed one
    instance while leaving its mirror open. Red-after-claim did not contest; then a
    green run of a DIFFERENT check cleared an unrelated contest; then red-then-green-
    then-claim produced no contest at all, because only the LAST ledger entry was read.

    It is now derived -- contested == (a reuse stands as 'worked') AND (some check is
    currently red) -- recomputed from scratch on every event. These guards cover the
    orderings, which is what a derived value makes it possible to state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def red(self, cmd="python3 -m pytest", out="2 failed, 3 passed"):
        self.fx.track(self.fx.repo, dict(bash(cmd, stdout=out), session_id="s1"))

    def green(self, cmd="python3 -m pytest", out="5 passed"):
        self.fx.track(self.fx.repo, dict(bash(cmd, stdout=out), session_id="s1"))

    def claim(self, outcome="worked"):
        self.fx.track(self.fx.repo, dict(applied(outcome), session_id="s1"))

    def test_a_green_other_check_between_the_red_and_the_claim_still_contests(self):
        """pytest red, ruff green, then the claim. Reading only verifications[-1] saw
        the ruff green and raised nothing, so the gate let the session end."""
        self.red()
        self.green("ruff check .", "All checks passed!")
        self.claim()
        self.assertIsNotNone(self.fx.state().get("contested"),
                             "an unrelated green check hid a still-red suite")
        self.assertEqual(self.fx.stop(self.fx.repo, "s1")["decision"], "block")

    def test_every_ordering_of_red_and_claim_contests(self):
        for order in ("red-then-claim", "claim-then-red"):
            with self.subTest(order=order):
                fx = EvidenceFixture(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, fx.repo, True)
                fx.track(fx.repo, dict(SEARCH, session_id="o"))
                red = dict(bash("python3 -m pytest", stdout="1 failed"), session_id="o")
                clm = dict(applied("worked"), session_id="o")
                for ev in ((red, clm) if order == "red-then-claim" else (clm, red)):
                    fx.track(fx.repo, ev)
                self.assertIsNotNone(
                    fx.state_files("o")["repo"].get("contested"), order)

    def test_a_pytest_collection_error_is_a_failure(self):
        """`5 passed, 1 error` is pytest reporting it could not even collect a module.
        Classified GREEN, a re-run reporting it would clear a real contest."""
        self.red()
        self.claim()
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest", stdout="5 passed, 1 error in 0.9s"),
            session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"),
                             "a collection error was read as a passing run")

    def test_a_rejected_apply_call_is_not_a_claim(self):
        """REQ-026 makes an omitted `outcome` an error. Treating the rejection as a
        `worked` claim would block the session over a reuse the database never saw."""
        self.red()
        self.fx.track(self.fx.repo, {
            "tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
            "session_id": "s1", "tool_input": {"keys": ["k1"]},
            "tool_response": {"result": {"error": "outcome is required",
                                         "error_code": "outcome_required"}}})
        self.assertIsNone(self.fx.state().get("contested"),
                          "a rejected call was recorded as a success")
        self.assertEqual(self.fx.state().get("worked_keys", []), [])

    def test_a_long_whitespace_heavy_command_clears_its_own_contest(self):
        """The stored form was normalised on one side and truncated on the other."""
        cmd = "cd pkg   &&   python3 -m pytest " + "-k not_slow " * 30
        self.assertGreater(len(cmd), 300)
        self.red(cmd, "4 failed")
        self.claim()
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.green(cmd, "7 passed")
        self.assertIsNone(self.fx.state().get("contested"),
                          "re-running the same long command did not clear it")


class TestTheGateStaysSatisfiable(unittest.TestCase):
    """Fifth review round. The theme is the failure mode this project has already
    shipped once: a gate that cannot be satisfied. Counting only applied_keys made the
    apply gate demand that every surfaced key be applied, which its own message
    forbade. Keying contests on the exact command line was the same mistake in a new
    place -- re-run the suite with one more flag and the contest stood forever, leaving
    a fabricated `outcome='failed'` as the only exit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def _contest(self, cmd="python3 -m pytest -q 2>&1 | tail -20"):
        self.fx.track(self.fx.repo, dict(bash(cmd, stdout="3 failed"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_the_same_check_in_another_form_clears_the_contest(self):
        for green in ("python3 -m pytest",
                      ".venv/bin/python -m pytest tests/foo.py",
                      "cd pkg && uv run pytest -k fast",
                      "pytest -q 2>&1 | tail -5"):
            with self.subTest(green=green):
                self._contest()
                self.fx.track(self.fx.repo,
                              dict(bash(green, stdout="9 passed"), session_id="s1"))
                self.assertIsNone(self.fx.state().get("contested"),
                                  f"{green!r} is the same check and did not clear it")

    def test_a_different_check_going_green_still_does_not_clear_it(self):
        """The other half. Identity is the middle ground, not a licence."""
        self._contest()
        self.fx.track(self.fx.repo, dict(
            bash("ruff check .", stdout="All checks passed!"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_a_shell_backgrounded_run_is_not_a_verdict(self):
        """`pytest -q &` returns immediately with status 0, suite still running.
        run_in_background and backgroundTaskId were guarded; `&` was not, so an
        unfinished suite logged GREEN -- and a false green clears a real contest."""
        self._contest()
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest &"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_every_rejection_shape_is_refused(self):
        """A rejected call stored nothing, so it is not a claim. Checking only the
        exit-code form missed the shape THIS change produces: REQ-026's
        `outcome_required` error would have been recorded as a success."""
        shapes = (
            {"result": {"error": "outcome is required",
                        "error_code": "outcome_required"}},
            "Error: outcome is required: pass 'worked', or 'failed'",
            {"isError": True, "content": [{"type": "text", "text": "rejected"}]},
            {"content": [{"type": "text",
                          "text": '{"error_code": "invalid_outcome"}'}]},
        )
        for shape in shapes:
            with self.subTest(shape=str(shape)[:40]):
                fx = EvidenceFixture(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, fx.repo, True)
                fx.track(fx.repo, dict(SEARCH, session_id="r"))
                fx.track(fx.repo, dict(
                    bash("python3 -m pytest", stdout="1 failed"), session_id="r"))
                fx.track(fx.repo, {
                    "tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
                    "session_id": "r", "tool_input": {"keys": ["k1"]},
                    "tool_response": shape})
                st = fx.state_files("r")["repo"]
                self.assertIsNone(st.get("contested"))
                self.assertEqual(st.get("worked_keys", []), [])

    def test_a_successful_apply_is_still_recorded(self):
        """The converse, or the rejection check would make the mechanism inert."""
        self.fx.track(self.fx.repo, dict(
            bash("python3 -m pytest", stdout="1 failed"), session_id="s1"))
        self.fx.track(self.fx.repo, {
            "tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
            "session_id": "s1", "tool_input": {"keys": ["k1"], "outcome": "worked"},
            "tool_response": {"result": {"applied": [{"_key": "k1"}], "count": 1,
                                         "outcome": "worked"}}})
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_a_shortened_command_is_visibly_shortened(self):
        """A silently truncated command invites re-running what is DISPLAYED."""
        long_cmd = "cd pkg && python3 -m pytest " + "-k not_slow " * 30
        self._contest(long_cmd)
        reason = self.fx.stop(self.fx.repo, "s1")["reason"]
        self.assertIn("\u2026", reason, "the command was truncated with no ellipsis")


class TestTheEvidenceIsAttributedToTheRightCheck(unittest.TestCase):
    """Sixth review round. Three findings, all of them false GREENS -- which is the
    dangerous direction, because a false green does not merely fail to raise a
    contest, it RESOLVES one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def test_the_red_is_filed_against_the_check_that_failed(self):
        """`ruff check . && python3 -m pytest -q`: ruff passes, pytest fails, the shell
        returns 1. Taking the identity from the FIRST runner filed that red under
        `ruff check`, so re-running ruff alone cleared the contest with pytest still
        red. The status segment owns the exit status, so it owns the identity."""
        self.fx.track(self.fx.repo, dict(
            bash("ruff check . && python3 -m pytest -q", ok=False), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))
        self.assertEqual(list(self.fx.state()["red_checks"]), ["pytest"])
        self.fx.track(self.fx.repo, dict(
            bash("ruff check .", stdout="All checks passed!"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"),
                             "a green ruff cleared a contest that belonged to pytest")

    def test_an_invocation_that_asserts_nothing_is_not_evidence(self):
        """`pytest --collect-only`, `--version`, `cargo test --list` exit 0 whatever
        state the tree is in. Recorded GREEN under identity `pytest`, each one pops a
        genuine red check and clears a real contest."""
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest", stdout="2 failed"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))
        # -v is pytest's VERBOSE flag, not a version query. A case-insensitive
        # `-V` in this pattern excluded 528 genuine test runs in the measured
        # corpus -- a third of everything it touched -- and the regex read fine.
        for verbose in ("python3 -m pytest -v", "pytest -vv tests/"):
            with self.subTest(cmd=verbose):
                self.assertTrue(_TRACKER.is_verification(verbose),
                                f"{verbose!r} is a real test run")
        for cmd in ("python3 -m pytest --collect-only", "pytest --version",
                    "cargo test --list", "pytest --co -q"):
            with self.subTest(cmd=cmd):
                self.fx.track(self.fx.repo, dict(bash(cmd), session_id="s1"))
                self.assertIsNotNone(
                    self.fx.state().get("contested"),
                    f"{cmd!r} asserts nothing about the tree but cleared a contest")

    def test_a_legacy_file_cannot_resurrect_a_resolved_contest(self):
        """merge_legacy_state unions lists and never deletes the legacy file, so every
        later read re-offered whatever it held. `worked_keys` is the first value here
        that is ever REMOVED from -- recording outcome='failed' takes a key out of it --
        so an honest failure was undone and the stale `contested` restored verbatim.
        The gate then blocks on a resolved contest with no action that clears it: the
        unsatisfiable gate this project has already shipped once."""
        self.fx.track(self.fx.repo,
                      dict(bash("python3 -m pytest", stdout="1 failed"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        stale = dict(self.fx.state())
        self.assertIsNotNone(stale.get("contested"))
        # The agent does the honest thing and records the failure.
        self.fx.track(self.fx.repo, dict(applied("failed"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))
        # A legacy copy from before that still carries the claim and the contest.
        self.fx.write_state(self.fx.wt, "s1", stale)
        self.fx.track(self.fx.wt, dict(SEARCH, session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"),
                          "a legacy file resurrected a contest the agent had resolved")
        self.assertEqual(self.fx.state().get("worked_keys", []), [],
                         "a legacy file undid an honest outcome='failed'")
        self.assertIsNone(self.fx.stop(self.fx.repo, "s1"),
                          "the gate blocked on a contest with no way to clear it")



class TestAnEditScriptFollowedByChecksStaysClearable(unittest.TestCase):
    """Seventh round, from a real session. One Bash call applied edits with an inline
    script, then ran `ruff check ... && pytest ... | tail -2`. One test failed. Two
    checks, neither owning the exit status, so the red was keyed on the WHOLE line --
    and re-running that line repeats the edits, which an edit script asserting its
    old text refuses by design. The test was fixed and pytest went green; the gate
    still blocked. The unsatisfiable gate, a third time."""

    LINE = ("python3 - <<'EOF'\nfrom pathlib import Path\nedit('a.py')\nEOF\n"
            "cd backend && uv run ruff check app && uv run pytest tests -q 2>&1 | tail -2")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = EvidenceFixture(self._tmp.name)
        self.fx.track(self.fx.repo, dict(SEARCH, session_id="s1"))

    def _red(self):
        self.fx.track(self.fx.repo,
                      dict(bash(self.LINE, stdout="1 failed, 36 passed"), session_id="s1"))
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"))

    def test_the_red_is_filed_under_each_check_not_the_line(self):
        self._red()
        self.assertEqual(sorted(self.fx.state()["red_checks"]), ["pytest", "ruff check"])

    def test_re_running_each_check_clears_it(self):
        self._red()
        self.fx.track(self.fx.repo, dict(
            bash("cd backend && uv run pytest tests -q", stdout="37 passed"), session_id="s1"))
        self.assertIsNotNone(self.fx.state().get("contested"),
                             "pytest alone cleared a red ruff may own")
        self.fx.track(self.fx.repo, dict(
            bash("uv run ruff check app", stdout="All checks passed!"), session_id="s1"))
        self.assertIsNone(self.fx.state().get("contested"))
        self.assertIsNone(self.fx.stop(self.fx.repo, "s1"))

    def test_an_ambiguous_green_clears_nothing(self):
        """`ruff check . ; pytest | tail` saying "9 passed" proves pytest, not ruff."""
        self._red()
        self.fx.track(self.fx.repo, dict(
            bash("ruff check . ; pytest -q | tail -1", stdout="37 passed"), session_id="s1"))
        self.assertEqual(sorted(self.fx.state()["red_checks"]), ["pytest", "ruff check"])

    def test_a_red_an_older_version_keyed_on_the_line_is_refiled(self):
        """A session already stuck must come unstuck after the upgrade."""
        shown = _TRACKER.norm_command(self.LINE)
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        st = dict(self.fx.state())
        st["red_checks"] = {shown: {"cmd": shown, "check": shown, "exit": None,
                                    "at": "2026-11-20T00:26:25Z"}}
        st["contested"] = {"keys": ["k1"], "cmd": shown, "exit": None,
                           "at": "2026-11-20T00:26:25Z", "claimed": "worked"}
        self.fx.write_state(self.fx.repo, "s1", st)
        for green in (("uv run pytest tests", "37 passed"),
                      ("ruff check app", "All checks passed!")):
            self.fx.track(self.fx.repo, dict(bash(green[0], stdout=green[1]), session_id="s1"))
        self.assertEqual(self.fx.state().get("red_checks"), {})
        self.assertIsNone(self.fx.state().get("contested"))

    def _legacy_truncated_red(self, ran=()):
        """A whole-line red whose stored line was cut before it named any check."""
        long_line = ("python3 - <<'EOF'\n" + "edit('a.py')\n" * 200
                     + "EOF\nruff check app && pytest tests -q | tail -2")
        shown = _TRACKER.norm_command(long_line)
        self.assertEqual(_TRACKER.check_identities(shown), [shown],
                         "fixture must lose every check to truncation")
        self.fx.track(self.fx.repo, dict(applied("worked"), session_id="s1"))
        st = dict(self.fx.state())
        st["verifications"] = [
            {"at": "2026-11-20T00:20:00Z", "cmd": shown, "check": shown, "ok": False,
             "exit": None}] + [
            {"at": "2026-11-20T00:10:00Z", "cmd": c, "check": c, "ok": True, "exit": 0}
            for c in ran]
        st["red_checks"] = {shown: {"cmd": shown, "check": shown, "exit": None,
                                    "at": "2026-11-20T00:26:25Z"}}
        st["contested"] = {"keys": ["k1"], "cmd": shown, "exit": None,
                           "at": "2026-11-20T00:26:25Z", "claimed": "worked"}
        self.fx.write_state(self.fx.repo, "s1", st)

    def test_a_truncated_legacy_red_is_refiled_under_the_checks_this_session_ran(self):
        """Re-filing read the checks from the stored line, which is cut at MAX_CMD;
        a long edit script left none, so the red stayed keyed on the line and a green
        pytest could not clear it."""
        self._legacy_truncated_red()
        self.fx.track(self.fx.repo, dict(
            bash("uv run pytest tests", stdout="37 passed"), session_id="s1"))
        self.assertEqual(self.fx.state().get("red_checks"), {})
        self.assertIsNone(self.fx.state().get("contested"))
        self.assertIsNone(self.fx.stop(self.fx.repo, "s1"))

    def test_a_truncated_legacy_red_needs_every_check_the_session_ran(self):
        """Strictness kept: filed under each check run, so one green clears only its own."""
        self._legacy_truncated_red(ran=("ruff check", "mypy"))
        self.fx.track(self.fx.repo, dict(
            bash("uv run pytest tests", stdout="37 passed"), session_id="s1"))
        self.assertEqual(sorted(self.fx.state()["red_checks"]), ["mypy", "ruff check"])
        self.assertIsNotNone(self.fx.state().get("contested"))
        for green in (("ruff check app", "All checks passed!"),
                      ("mypy src", "Success: no issues found in 3 source files")):
            self.fx.track(self.fx.repo, dict(bash(green[0], stdout=green[1]), session_id="s1"))
        self.assertEqual(self.fx.state().get("red_checks"), {})
        self.assertIsNone(self.fx.state().get("contested"))

    def test_a_truncated_legacy_red_stays_put_when_no_check_has_run(self):
        """Nothing to file it under; a non-check command must not drop it."""
        self._legacy_truncated_red()
        before = dict(self.fx.state()["red_checks"])
        self.assertEqual(_TRACKER._refile_whole_line_reds(before, []), before)

if __name__ == "__main__":
    unittest.main()
