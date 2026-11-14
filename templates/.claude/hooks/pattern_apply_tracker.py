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
    # Leading `FOO=1 BAR=2` assignments, then an optional environment-manager prefix.
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*" + _RUNNER + r"(?:"
    r"(?:python[0-9.]*\s+-m\s+)?(?:pytest|unittest|tox|nox)\b"
    r"|(?:npm|yarn|pnpm|bun|deno)\s+(?:run\s+)?(?:test|lint|typecheck|check|build)\b"
    r"|cargo\s+(?:test|check|clippy|build)\b"
    r"|go\s+(?:test|vet|build)\b"
    r"|(?:make|just)\s+(?:test|tests|check|lint|verify|ci|typecheck)\b"
    r"|(?:ruff|mypy|pyright|pylint|flake8|eslint|tsc|shellcheck|bats|rspec|jest|vitest|phpunit)\b"
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

#: The `&` in a redirect is not a separator. Missing this cut `ruff check . > out 2>&1`
#: at the `&`, leaving the last segment as `1` -- so redirecting output to a file, the
#: very thing one does INSTEAD of piping through tail, stopped counting as a check.
_REDIRECT_AMP = re.compile(r"([<>])&")

#: Bounded, like apply_log server-side. The gate only ever reads the last entry.
MAX_VERIFICATIONS = 20


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
    masked = _REDIRECT_AMP.sub(r"\1 ", _QUOTED.sub(" ", command or ""))
    parts = [p for p in _SEPARATORS.split(masked) if p.strip()]
    return parts[-1].strip() if parts else ""


def is_verification(command):
    """True when the exit status of `command` is a verdict on the tree."""
    return bool(VERIFY_RE.search(status_segment(command)))


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
    if isinstance(resp, str):
        match = re.match(r"\s*(?:Error:\s*)?Exit code\s+(\d+)", resp)
        if match:
            code = int(match.group(1))
            return (code == 0), code
        return None, None
    if isinstance(resp, dict):
        if resp.get("interrupted"):
            return None, None   # killed by the user or a timeout: no verdict
        # A backgrounded run returns the moment it is launched. Its exit status is not
        # in this payload and the suite has not finished, so reading it as a pass would
        # log a green check for a test run that may be about to fail.
        if resp.get("backgroundTaskId") or resp.get("background_task_id"):
            return None, None
        if "stdout" in resp or "stderr" in resp:
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
        "keys": claimed, "cmd": command[:200], "exit": code,
        "at": when, "claimed": "worked",
    }
    return state["contested"]


def _clear_on_green(state, command):
    """A green re-run of THE SAME command supersedes the evidence.

    Any green at all used to clear it, so `ruff check .` resolved a contest raised over
    a still-red `pytest` -- while the block message says "re-run that check". The gate
    may block only once per stop chain, so a contest can never trap a session even when
    this is strict.
    """
    contested = state.get("contested")
    if contested and status_segment(contested.get("cmd", "")) == status_segment(command):
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
        entries.append({"at": _now(), "cmd": command[:200], "ok": bool(passed),
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
            print(
                "[SHARED-MEMORY APPLY GATE] outcome='{claimed}' stands against a RED "
                "check: `{cmd}` exited {exit}. By this tool's own definition a pattern "
                "FAILED if the checks it was meant to make pass are still failing. "
                "Re-run that exact check: if it passes, this clears on its own and the "
                "claim stands. If it still fails, call pattern-applied again with "
                "outcome='failed' for {keys}.".format(
                    claimed=contested.get("claimed", "worked"),
                    cmd=(contested.get("cmd") or "")[:120],
                    exit=contested.get("exit"),
                    keys=", ".join(contested.get("keys") or []) or "that key",
                )
            )
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
