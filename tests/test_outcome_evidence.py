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

SEARCH = {"tool_name": "mcp__arangodb-memory-mcp__pattern-search",
          "tool_response": {"patterns": [{"_key": "k1"}]}}


def applied(outcome="worked", keys=("k1",)):
    return {"tool_name": "mcp__arangodb-memory-mcp__pattern-applied",
            "tool_input": {"keys": list(keys), "outcome": outcome}}


def bash(command, ok=True, code=1, interrupted=False):
    """A PostToolUse Bash payload in the two shapes Claude Code actually produces.

    Confirmed against stored transcripts, not assumed: a command that SUCCEEDS comes
    back as a dict with stdout/stderr, and one that FAILS has its result replaced by
    the string "Error: Exit code N ...". That difference is structural, which is why
    the hook reads it instead of guessing from the words in stdout -- a passing run
    that prints "FAILED" in a summary line would fool any text heuristic.
    """
    if ok:
        response = {"stdout": "ok", "stderr": "", "interrupted": interrupted,
                    "isImage": False, "noOutputExpected": False}
    else:
        response = f"Error: Exit code {code}\nsome failing output"
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

    def test_bash_before_any_pattern_search_writes_nothing(self):
        """This hook now runs on EVERY Bash call in every bootstrapped project. With no
        search this session there is no claim to check, so it must not create state (or
        pay for a git subprocess) on the way to doing nothing."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fresh = EvidenceFixture(tmp.name)
        fresh.track(fresh.repo, dict(bash("pytest", ok=False), session_id="never"))
        self.assertEqual(fresh.state_files("never"), {})


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
        target = 'if last and not last[-1].get("ok"):'
        self.assertIn(target, source, "mutation target vanished; this test is inert")
        mutated = source.replace(target, "if False:")
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


if __name__ == "__main__":
    unittest.main()
