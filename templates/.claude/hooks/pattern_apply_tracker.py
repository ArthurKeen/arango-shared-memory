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
#: Anchored to the start of a command (line start, or after a shell separator) so that
#: `grep -rn "pytest" .` -- which proves nothing -- is not read as a test run. An
#: optional environment-manager prefix covers `uv run pytest` and friends.
_RUNNER = r"(?:(?:uv|poetry|pdm|hatch|pipenv|rye|npx|pnpm\s+dlx)\s+run\s+|uv\s+run\s+|npx\s+)?"
VERIFY_RE = re.compile(
    r"(?:^|[;&|]\s*|\$\(\s*|`\s*)\s*" + _RUNNER + r"(?:"
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

#: Bounded, like apply_log server-side. The gate only ever reads the last entry.
MAX_VERIFICATIONS = 20


def _event_id(payload: dict[str, Any]) -> str:
    raw = payload.get("session_id") or payload.get("conversation_id") or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(raw))[:120]


def _path(payload: dict[str, Any]) -> str:
    sid = _event_id(payload)
    merge_legacy_state(sid)   # fold in state a pre-fix run split elsewhere
    return os.path.join(state_dir(), f"{sid}.json")


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _keys(value: Any) -> list[str]:
    value = _decode(value)
    found: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("_key"), str):
            found.append(value["_key"])
        for child in value.values():
            found.extend(_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_keys(child))
    return list(dict.fromkeys(found))


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: str, state: dict[str, Any]) -> None:
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


def verdict(payload: dict[str, Any]):
    """(passed, exit_code) for a Bash call, or (None, None) when it is not a verdict.

    Shape-based, not text-based. Claude Code hands a PostToolUse hook the tool result
    as a dict with stdout/stderr when the command SUCCEEDED, and replaces it with the
    string "Error: Exit code N ..." when it FAILED. Confirmed against stored transcripts
    rather than assumed, because a text heuristic over stdout ("FAILED", "error") would
    fire on a passing run that merely prints the word.

    Deliberately conservative: anything it cannot read as a clean pass or a clean
    failure returns (None, None) and is not evidence. A guess here would contest an
    honest claim, which is worse than missing a dishonest one.
    """
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
        if "stdout" in resp or "stderr" in resp:
            return True, 0
    return None, None


def _record_verification(state: dict[str, Any], command: str, passed: bool,
                         code: int, when: str) -> None:
    entries = [e for e in state.get("verifications", []) if isinstance(e, dict)]
    entries.append({"at": when, "cmd": command[:200], "ok": bool(passed), "exit": code})
    state["verifications"] = entries[-MAX_VERIFICATIONS:]
    if passed:
        # Evidence superseded. Re-running the check and getting green is the one way
        # to clear a contest without recording a failure -- and it is an action, not
        # an assertion, which is the entire point of this mechanism.
        state.pop("contested", None)


def _now(payload: dict[str, Any]) -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update(payload: dict[str, Any]) -> dict[str, Any]:
    """Fold one PostToolUse event into the session state.

    Returns {"surfaced": [...], "contested": {...} | None} for the caller's message.
    """
    name = str(payload.get("tool_name") or "").lower().replace("_", "-")
    out: dict[str, Any] = {"surfaced": [], "contested": None}

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
        if not isinstance(command, str) or not VERIFY_RE.search(command):
            return out
        passed, code = verdict(payload)
        if passed is None:
            return out
        state = _load(path)
        if not state.get("surfaced_keys"):
            return out
        _record_verification(state, command, passed, code, _now(payload))
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
            state.pop("contested", None)   # the claim already matches the evidence
        else:
            last = [e for e in state.get("verifications", []) if isinstance(e, dict)]
            if last and not last[-1].get("ok"):
                red = last[-1]
                out["contested"] = state["contested"] = {
                    "keys": list(applied),
                    "cmd": red.get("cmd", ""),
                    "exit": red.get("exit"),
                    "at": red.get("at"),
                    "claimed": claimed,
                }
    if state:
        _save(_path(payload), state)
    return out


def main() -> int:
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
                "[SHARED-MEMORY APPLY GATE] outcome='{claimed}' recorded while the last "
                "check was RED: `{cmd}` exited {exit}. By this tool's own definition a "
                "pattern FAILED if the checks it was meant to make pass are still "
                "failing. Re-run that check: if it passes, this notice clears on its own "
                "and the claim stands. If it still fails, call pattern-applied again with "
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
