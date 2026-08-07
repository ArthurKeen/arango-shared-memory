#!/usr/bin/env python3
"""Claude PostToolUse hook: track surfaced keys until reuse is attributed.

The hook deliberately never writes an apply event itself. Search output proves
that a pattern was viewed, not that it influenced the solution.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from typing import Any

STATE_DIR = os.path.join(".claude", ".shared-memory-sessions")


def _event_id(payload: dict[str, Any]) -> str:
    raw = payload.get("session_id") or payload.get("conversation_id") or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(raw))[:120]


def _path(payload: dict[str, Any]) -> str:
    return os.path.join(STATE_DIR, f"{_event_id(payload)}.json")


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
    os.makedirs(os.path.dirname(path), exist_ok=True)
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


def update(payload: dict[str, Any]) -> list[str]:
    name = str(payload.get("tool_name") or "").lower().replace("_", "-")
    state = _load(_path(payload))
    surfaced: list[str] = []
    if name.endswith("pattern-search"):
        surfaced = _keys(
            payload.get("tool_response", payload.get("tool_output", payload.get("result_json")))
        )
        state["surfaced_keys"] = list(
            dict.fromkeys([*state.get("surfaced_keys", []), *surfaced])
        )
    elif name.endswith("pattern-applied"):
        tool_input = _decode(payload.get("tool_input") or {})
        applied = tool_input.get("keys", []) if isinstance(tool_input, dict) else []
        state["applied_keys"] = list(
            dict.fromkeys([*state.get("applied_keys", []), *applied])
        )
    if state:
        _save(_path(payload), state)
    return surfaced


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        surfaced = update(payload)
        if surfaced:
            print(
                "[SHARED-MEMORY APPLY GATE] If any surfaced result is actually reused, "
                "call pattern-applied immediately with only the reused key(s): "
                + ", ".join(surfaced)
            )
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
