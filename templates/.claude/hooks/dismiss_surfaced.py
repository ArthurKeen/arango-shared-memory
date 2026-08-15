#!/usr/bin/env python3
"""Record surfaced-but-not-reused pattern keys, so the apply gate is satisfiable.

The apply gate blocks while `surfaced_keys - applied_keys` is non-empty, while its
own message correctly forbids marking every result as applied. Those two rules can
only both hold if there is a third state: reviewed, and deliberately NOT reused —
the normal outcome for most hits of a search that returns 8 results. Without it the
gate is unsatisfiable, and because `stop_hook_active` only suppresses the block once
per stop chain (not across turns), it re-fires every turn. That reads as a nagging
gate rather than a bug, so the operator learns to ignore it — which also disables
the genuine signal.

This records that third state. It never touches `shared_patterns`, so it cannot
inflate usage_count or the learned success-rate ranking: dismissal is local audit
state, not a memory write. Reuse is still attributed only by `pattern-applied`.

The Claude and Cursor gates keep their session state in different directories, so
the session file is located rather than assumed — one copy of this tool serves both.

Usage:
    python3 .claude/hooks/dismiss_surfaced.py <session_id> <key> [<key> ...]
    python3 .claude/hooks/dismiss_surfaced.py --list
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

# Claude and Cursor each namespace their own session state; order is search order.
STATE_DIRS = (
    os.path.join(".claude", ".shared-memory-sessions"),
    os.path.join(".cursor", ".shared-memory-sessions"),
)


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", session_id)[:120]


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _find_state(session_id: str) -> str | None:
    """The existing state file for this session, in whichever runtime wrote it."""
    name = f"{_safe(session_id)}.json"
    for directory in STATE_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _save(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dismiss-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _pending(state: dict) -> list[str]:
    resolved = set(state.get("applied_keys", [])) | set(state.get("dismissed_keys", []))
    return [k for k in state.get("surfaced_keys", []) if k not in resolved]


def _list() -> int:
    for directory in STATE_DIRS:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            state = _load(os.path.join(directory, name))
            print(
                f"{directory}/{name}: surfaced={len(state.get('surfaced_keys', []))} "
                f"applied={len(state.get('applied_keys', []))} "
                f"dismissed={len(state.get('dismissed_keys', []))} "
                f"pending={len(_pending(state))}"
            )
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    if args[0] == "--list":
        return _list()

    session_id, keys = args[0], args[1:]
    if not keys:
        print("No keys given.", file=sys.stderr)
        return 2

    path = _find_state(session_id)
    if path is None:
        # No state at all means nothing was ever surfaced for this session, so every
        # key would be a silently-wrong dismissal.
        print(f"No surfaced state for session {session_id!r}, refusing.", file=sys.stderr)
        return 1

    state = _load(path)
    surfaced = set(state.get("surfaced_keys", []))
    applied = set(state.get("applied_keys", []))

    unknown = [k for k in keys if k not in surfaced]
    if unknown:
        # Refuse silently-wrong input: dismissing a key that was never surfaced
        # would mask a future real gap instead of recording a real decision.
        print(f"Not surfaced in this session, refusing: {', '.join(unknown)}", file=sys.stderr)
        return 1
    clash = [k for k in keys if k in applied]
    if clash:
        print(f"Already attributed as applied, refusing: {', '.join(clash)}", file=sys.stderr)
        return 1

    state["dismissed_keys"] = sorted(set(state.get("dismissed_keys", [])) | set(keys))
    _save(path, state)
    print(
        f"dismissed {len(keys)}; applied {len(applied)}; pending {len(_pending(state))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
