#!/usr/bin/env python3
"""Claude PostToolUse hook: track surfaced keys, and check apply claims against evidence.

Two jobs, one state file, because five independent writers of that directory is how
the gate and the trackers came to disagree once already (see hook_paths.state_dir).

1. Surfacing/attribution. The hook deliberately never writes an apply event itself.
   Search output proves that a pattern was viewed, not that it influenced the solution.

2. Verification ledger (added 2026-11-14). Every `outcome` ever recorded is 'worked':
   288 of 288 across 91 patterns, so the success-rate multiplier in the search ranking
   -- (0.6 + 0.4*succ), with succ pinned at 1 -- is a constant 1.4 that ranks nothing.
   Two rounds of documenting the 'failed' option changed nothing, because an outcome an
   agent asserts costs nothing to assert. So this hook stops relying on the assertion:
   it records whether the project's own checks were passing, and contests a 'worked'
   claim made while the last check was red.

   This is NOT the "fully inferred pattern-applied write" that docs/implementation-plan.md
   excludes. Nothing here invents an apply event or infers that a surfaced result
   influenced anything -- the agent still declares the reuse. Only the SIGN of a claim
   the agent already made is checked, against evidence the agent did not produce.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from typing import Any

# The sibling lives beside this file; add its directory for importers that load it by path.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from hook_paths import (  # noqa: E402
    ensure_private_dir, merge_legacy_state, state_dir)

# Resolved per call, not at import: hook_paths.state_dir() reads
# CLAUDE_PROJECT_DIR and the main checkout, so a session that moves into a
# worktree keeps writing to the one place the gate will read.

#: Commands whose exit status is a verdict on the working tree rather than a lookup.
_RUNNER = r"(?:(?:uv|poetry|pdm|hatch|pipenv|rye|pnpm\s+dlx)\s+run\s+|uv\s+run\s+|npx\s+)?"
VERIFY_RE = re.compile(
    # Always applied with .match() against ONE segment, so the command position is
    # enforced by the match itself; a leading ^ here would be redundant and, worse,
    # misleading -- removing either alone is a no-op, which briefly made a guard for
    # this rule look like it was passing when nothing was protecting it.
    # Leading `FOO=1 BAR=2` assignments, then an optional environment-manager prefix.
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*" + _RUNNER + r"(?:\S*/)?(?:"
    r"(?:python[0-9.]*\s+-m\s+)?(?:pytest|unittest|tox|nox)\b"
    r"|(?:npm|yarn|pnpm|bun|deno)\s+(?:run\s+)?(?:test|lint|typecheck|check|build)\b"
    r"|cargo\s+(?:test|check|clippy|build)\b"
    r"|go\s+(?:test|vet|build)\b"
    r"|(?:make|just)\s+(?:test|tests|check|lint|verify|ci|typecheck)\b"
    r"|ruff\s+check\b"
    r"|(?:mypy|pyright|pylint|flake8|eslint|tsc|shellcheck|bats|rspec|jest|vitest|phpunit)\b"
    r"|dotnet\s+test\b|mvn\s+[\w:-]*test|gradlew?\s+[\w:-]*test"
    r"|git\s+bisect\s+run\b"
    r")",
    re.IGNORECASE,
)

#: Quoted spans are masked before the command is parsed. Without this, the `|` inside
#: `grep -nE "ruff|mypy|pytest" ci.yml` reads as a pipeline separator and the segment
#: `mypy|pytest" ci.yml` looks like a type check -- so grep exiting 1 on no match would
#: be stored as a RED project check, and an honest success would then be contested and
#: the session blocked naming a *grep* as the failing suite. A false contest is the one
#: failure mode that actively pushes an agent toward recording a fabricated 'failed'.
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")

#: `||` and `&&` before the single-character forms, or they split in the wrong place.
_SEPARATORS = re.compile(r"\|\||&&|[;|&\n]")

#: A program can also start just inside `$(`, a backtick or a subshell paren.
_SUBSHELL = re.compile(r"\$\(|[`(){}]")

#: The `&` in a redirect is not a separator. Missing this cut `ruff check . > out 2>&1`
#: at the `&`, leaving the last segment as `1` -- so redirecting output to a file, the
#: very thing one does INSTEAD of piping through tail, stopped counting as a check.
_REDIRECT_AMP = re.compile(r"([<>])&")

#: Bounded, like apply_log server-side. The gate only ever reads the last entry.
MAX_VERIFICATIONS = 20

#: One truncation length for every place a command is stored, because _clear_on_green
#: compares a contest's command against a later run's. Two different lengths meant a
#: check whose line exceeded the shorter one could never be cleared by re-running it,
#: leaving a false `failed` as the only way out of the gate. Generous: this is a local
#: state file holding at most MAX_VERIFICATIONS entries.
MAX_CMD = 1000


def _masked(command):
    """The command with quoted spans blanked, for structural inspection."""
    return _REDIRECT_AMP.sub(r"\1 ", _QUOTED.sub(" ", command or ""))


def status_segment(command):
    """The part of a command line whose exit status the shell actually reports.

    `pytest -q 2>&1 | tail -20` exits 0 when tail does, however many tests failed --
    so reading that line as a passing suite records a false GREEN, and a false green
    does not merely miss a contest, it CLEARS one. Piping test output through tail or
    head is the dominant form in real transcripts, which made this the likeliest way
    for the whole mechanism to report success while measuring nothing.

    So only the last segment counts. `cd foo && pytest` still qualifies; `pytest |
    tail` does not, and is treated as no evidence rather than as a pass. Conservative
    on purpose: `set -o pipefail` would change the answer and is not knowable here.
    """
    masked = _masked(command)
    parts = [p for p in _SEPARATORS.split(masked) if p.strip()]
    return parts[-1].strip() if parts else ""


def segments(command):
    """Every command position in the script, in order.

    Splitting on the shell separators AND on command-substitution/subshell openers
    gives one entry per place a program can start. `cd X && python3 -m pytest` yields
    `cd X` and `python3 -m pytest`.
    """
    masked = _SUBSHELL.sub(" ", _masked(command))
    return [p.strip() for p in _SEPARATORS.split(masked) if p.strip()]


def mentions_runner(command):
    """True when the command invokes a check somewhere, at any shell depth.

    Deliberately NOT "the check is the thing whose exit status we get". Measured
    against 69,167 real Bash calls from local transcripts: 13,299 invoke a runner, and
    requiring it to own the exit status recognised 10 of them -- 0.1%. Real agent
    commands are whole shell programs (`cd X && ... <<PY ... PY`, loops, `S=$(mktemp)`
    preambles), so asking where in the script the runner sits is the wrong question.
    Quote masking still applies, so `grep -nE "ruff|mypy|pytest" ci.yml` is a lookup.
    """
    return any(VERIFY_RE.match(seg) for seg in segments(command))


def status_is_the_checks(command):
    """True when the shell's exit status is the check's own, so it can be trusted."""
    return bool(VERIFY_RE.match(status_segment(command)))


def is_verification(command):
    """Kept as the ledger's gate: does this command run a check at all."""
    return mentions_runner(command)


def _event_id(payload):
    raw = payload.get("session_id") or payload.get("conversation_id") or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(raw))[:120]


def _path(payload):
    sid = _event_id(payload)
    merge_legacy_state(sid)   # fold in state a pre-fix run split elsewhere
    return os.path.join(state_dir(), f"{sid}.json")


def _decode(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _keys(value):
    value = _decode(value)
    found = []
    if isinstance(value, dict):
        if isinstance(value.get("_key"), str):
            found.append(value["_key"])
        for child in value.values():
            found.extend(_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_keys(child))
    return list(dict.fromkeys(found))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path, state):
    ensure_private_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".apply-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


#: What a check SAYS about itself, for the common case where its exit status is lost.
#: These are summary forms emitted by the runners named in VERIFY_RE, not words that
#: happen to appear in output: a passing run that prints "FAILED" inside a diff or a
#: docstring matches none of them. Consulted only when the command demonstrably invokes
#: a runner, which keeps `cat failures.log` from being read as a test result.
_RED_OUTPUT = re.compile(
    r"FAILED\s*\((?:failures|errors)="            # unittest
    r"|\b[1-9]\d*\s+failed\b"                    # pytest / jest summary line
    r"|^=+\s*FAILURES\s*=+"                       # pytest section header
    r"|\btest result:\s*FAILED\b"                 # cargo
    r"|\berror\s+TS\d+"                           # tsc
    r"|\bFound\s+[1-9]\d*\s+error"               # ruff / tsc
    r"|^FAIL\s"                                    # go test / jest
    r"|\b[1-9]\d*\s+problems?\s*\(\s*[1-9]\d*\s+error",   # eslint
    re.MULTILINE,
)
_GREEN_OUTPUT = re.compile(
    r"^OK\s*(?:\(|$)"                              # unittest
    r"|\b\d+\s+passed\b"                          # pytest / jest
    r"|\btest result:\s*ok\b"                      # cargo
    r"|\bAll checks passed"                         # ruff
    r"|\bFound 0 errors"                            # tsc
    r"|^PASS\s",                                    # jest / go
    re.MULTILINE,
)


def _response_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr"))
    return ""


def classify_output(text):
    """True/False from what a runner reported, or None when it said nothing clear.

    Red is tested first so "1 failed, 7 passed" reads as a failure rather than a pass.
    """
    if not text:
        return None
    if _RED_OUTPUT.search(text):
        return False
    if _GREEN_OUTPUT.search(text):
        return True
    return None


def verdict(payload):
    """(passed, exit_code) for a Bash call, or (None, None) when it is not a verdict.

    Shape-based, not text-based. Claude Code hands a PostToolUse hook the tool result
    as a dict with stdout/stderr when the command SUCCEEDED, and replaces it with the
    string "Error: Exit code N ..." when it FAILED. Confirmed against stored
    transcripts rather than assumed, because a text heuristic over stdout ("FAILED",
    "error") would fire on a passing run that merely prints the word in a summary line.

    Deliberately conservative: anything it cannot read as a clean pass or a clean
    failure returns (None, None) and is not evidence. A guess here would contest an
    honest claim, which is worse than missing a dishonest one.
    """
    tool_input = _decode(payload.get("tool_input") or {})
    if isinstance(tool_input, dict) and tool_input.get("run_in_background"):
        return None, None
    resp = payload.get("tool_response", payload.get("tool_output"))
    text = _response_text(resp)

    # A timeout is not a failing suite. It arrives as the string "Error: Exit code 143
    # \nCommand timed out after 2m 0s" -- the `interrupted` key is a dict-shape flag
    # that never accompanies it (0 of 29,882 dict results in local transcripts carried
    # it, while all 18 timeouts came through as the 143 string). Read as a clean RED,
    # a pytest run that merely exceeded the Bash timeout would contest an honest claim.
    if re.search(r"Command timed out|timed out after", text):
        return None, None
    if isinstance(resp, dict):
        if resp.get("interrupted"):
            return None, None   # killed by the user: no verdict
        # A backgrounded run returns the moment it is launched. Its exit status is not
        # in this payload and the suite has not finished, so reading it as a pass would
        # log a green check for a test run that may be about to fail.
        if resp.get("backgroundTaskId") or resp.get("background_task_id"):
            return None, None

    command = ""
    tool_input = _decode(payload.get("tool_input") or {})
    if isinstance(tool_input, dict):
        command = tool_input.get("command") or ""

    # Did the command as a whole fail? Only the string shape carries that.
    failure = (re.match(r"\s*(?:Error:\s*)?Exit code\s+(\d+)", resp)
               if isinstance(resp, str) else None)
    code = int(failure.group(1)) if failure else None
    said = classify_output(text)

    # A runner reporting its own failure is believed whatever the shell returned: a
    # pipe through tail hides a red suite behind exit 0, which is the case this whole
    # mechanism exists to catch.
    if said is False:
        return False, code

    # A green summary is NOT believed when the command as a whole failed. A compound
    # command prints each stage's output, so `make check` emitting unittest's "OK"
    # before shellcheck fails, or `cargo test && cargo clippy`, would otherwise be
    # recorded GREEN -- and re-running that same still-failing command would then
    # CLEAR a real contest, which is worse than recording nothing.
    if said is True:
        return (None, None) if failure else (True, 0)

    # No summary to read. Fall back to the exit status, and only where it IS the
    # check's own rather than a pager's or a later unrelated command's.
    if status_is_the_checks(command):
        if failure:
            return False, code
        if isinstance(resp, dict) and ("stdout" in resp or "stderr" in resp):
            return True, 0
    return None, None


def _contest(state, command, code, when):
    """Flag every still-standing 'worked' claim as contradicted by a red check.

    Evaluated on BOTH events, because either can come last. Computing it only when
    pattern-applied fires made the mechanism inert in the workflow the project itself
    prescribes -- templates/CLAUDE.md says to record reuse immediately, which puts the
    claim BEFORE the test run almost every time.
    """
    claimed = [k for k in state.get("worked_keys", []) if k]
    if not claimed:
        return None
    state["contested"] = {
        "keys": claimed, "cmd": (command or "")[:MAX_CMD], "exit": code,
        "at": when, "claimed": "worked",
    }
    return state["contested"]


def _same_command(left, right):
    """Whitespace-insensitive equality of two command lines.

    Comparing status_segment() instead looked tidier and was wrong: the status segment
    of ANY piped command is the pager, so a green `ruff check . 2>&1 | tail -20`
    cleared a contest raised by a red `pytest ... | tail -20` -- both segments being
    `tail -20`. That is the any-green-clears bug returning through a different door,
    for exactly the piping form this module calls dominant.
    """
    return " ".join((left or "").split())[:MAX_CMD] == \
           " ".join((right or "").split())[:MAX_CMD]


def _clear_on_green(state, command):
    """A green re-run of THE SAME command supersedes the evidence.

    Any green at all used to clear it, so `ruff check .` resolved a contest raised over
    a still-red `pytest` -- while the block message says "re-run that check". The gate
    may block only once per stop chain, so a contest can never trap a session even when
    this is strict.
    """
    contested = state.get("contested")
    if contested and _same_command(contested.get("cmd", ""), command):
        state.pop("contested", None)


def _clear_on_failure(state, keys):
    """Recording a failure resolves the contest only for the keys it actually covers."""
    state["worked_keys"] = [k for k in state.get("worked_keys", []) if k not in keys]
    contested = state.get("contested")
    if not contested:
        return
    remaining = [k for k in contested.get("keys", []) if k not in keys]
    if remaining:
        contested["keys"] = remaining
    else:
        state.pop("contested", None)


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update(payload):
    """Fold one PostToolUse event into the session state.

    Returns {"surfaced": [...], "contested": {...} | None} for the caller's message.
    """
    name = str(payload.get("tool_name") or "").lower().replace("_", "-")
    out = {"surfaced": [], "contested": None}

    # Defence in depth for an unanchored matcher. `Bash` as a regex alternative also
    # selects `BashOutput`, which polls a background task -- that name misses the fast
    # path below and would take the slow one (merge_legacy_state, a `git worktree list`
    # subprocess when CLAUDE_PROJECT_DIR is unset, and a state rewrite) on every poll.
    # settings.json is anchored now, but installed projects carry their own copy.
    if name != "bash" and not (name.endswith("pattern-search")
                               or name.endswith("pattern-applied")):
        return out

    if name == "bash":
        # Fast path: no git subprocess and no legacy merge, because this now runs on
        # EVERY Bash call in every bootstrapped project. With no state file there has
        # been no pattern-search this session, so there is no claim to check.
        sid = _event_id(payload)
        path = os.path.join(state_dir(), f"{sid}.json")
        if not os.path.isfile(path):
            return out
        tool_input = _decode(payload.get("tool_input") or {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if not isinstance(command, str) or not is_verification(command):
            return out
        passed, code = verdict(payload)
        if passed is None:
            return out
        state = _load(path)
        if not state.get("surfaced_keys"):
            return out
        entries = [e for e in state.get("verifications", []) if isinstance(e, dict)]
        entries.append({"at": _now(), "cmd": command[:MAX_CMD], "ok": bool(passed),
                        "exit": code})
        state["verifications"] = entries[-MAX_VERIFICATIONS:]
        if passed:
            _clear_on_green(state, command)
        else:
            out["contested"] = _contest(state, command, code, _now())
        _save(path, state)
        return out

    state = _load(_path(payload))
    if name.endswith("pattern-search"):
        surfaced = _keys(
            payload.get("tool_response", payload.get("tool_output", payload.get("result_json")))
        )
        state["surfaced_keys"] = list(
            dict.fromkeys([*state.get("surfaced_keys", []), *surfaced])
        )
        out["surfaced"] = surfaced
    elif name.endswith("pattern-applied"):
        tool_input = _decode(payload.get("tool_input") or {})
        applied = tool_input.get("keys", []) if isinstance(tool_input, dict) else []
        claimed = "worked"
        if isinstance(tool_input, dict) and tool_input.get("outcome"):
            claimed = str(tool_input["outcome"]).strip().lower()
        state["applied_keys"] = list(
            dict.fromkeys([*state.get("applied_keys", []), *applied])
        )
        if claimed == "failed":
            _clear_on_failure(state, set(applied))
        else:
            state["worked_keys"] = list(
                dict.fromkeys([*state.get("worked_keys", []), *applied])
            )
            last = [e for e in state.get("verifications", []) if isinstance(e, dict)]
            if last and not last[-1].get("ok"):
                red = last[-1]
                out["contested"] = _contest(state, red.get("cmd", ""),
                                            red.get("exit"), red.get("at") or _now())
    if state:
        _save(_path(payload), state)
    return out


def main():
    try:
        payload = json.load(sys.stdin)
        result = update(payload)
        if result["surfaced"]:
            print(
                "[SHARED-MEMORY APPLY GATE] If any surfaced result is actually reused, "
                "call pattern-applied immediately with only the reused key(s) and an "
                "explicit outcome ('worked', or 'failed' if you tried it and it did not "
                "help): " + ", ".join(result["surfaced"])
            )
        contested = result["contested"]
        if contested:
            exit_code = contested.get("exit")
            print(
                "[SHARED-MEMORY APPLY GATE] outcome='{claimed}' stands against a RED "
                "check: `{cmd}`{exit}. By this tool's own definition a pattern FAILED "
                "if the checks it was meant to make pass are still failing. Re-run "
                "that exact check: if it passes, this clears on its own and the claim "
                "stands. If it still fails, call pattern-applied again with "
                "outcome='failed' for {keys}.".format(
                    claimed=contested.get("claimed", "worked"),
                    cmd=(contested.get("cmd") or "")[:120],
                    # An output-classified red has no exit code of its own -- the
                    # runner reported the failure, the shell returned the pager's 0.
                    # "exited None" is not a thing that happened.
                    exit=f" exited {exit_code}" if exit_code is not None
                    else " reported failures",
                    keys=", ".join(contested.get("keys") or []) or "that key",
                )
            )
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
