#!/usr/bin/env python3
"""PostToolUse hook (Write|Edit) — queue drift markers for /prd-sync.

Reads the hook payload from stdin. Two triggers:
  - an implementation file was edited  -> marker  <epoch>_<basename>
  - the project's PRD file was edited  -> marker  prd_<epoch>_<basename>
    (requirements may have changed, not just code — a distinct signal)

PRD_FILE is parsed from ./CLAUDE.md at runtime, so this hook needs no
per-project rendering. Fail-open: any error exits 0 silently.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

SOURCE_EXT = re.compile(
    r"\.(ts|js|tsx|jsx|py|go|rs|java|cs|cpp|c|rb|php|swift|kt)$", re.IGNORECASE)


def prd_file_from_claude_md(path="CLAUDE.md"):
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r"PRD_FILE:\s*(\S+)", fh.read())
        if m and not m.group(1).startswith("<"):
            return m.group(1)
    except OSError:
        pass
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    tool_input = payload.get("tool_input", payload) or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0

    base = os.path.basename(file_path)
    prd = prd_file_from_claude_md()
    is_prd = bool(prd) and (
        os.path.normpath(file_path).endswith(os.path.normpath(prd))
        or base == os.path.basename(prd))
    is_source = bool(SOURCE_EXT.search(file_path))
    if not (is_prd or is_source):
        return 0

    os.makedirs(".prd-drift-queue", exist_ok=True)
    prefix = "prd_" if is_prd else ""
    marker = os.path.join(".prd-drift-queue", f"{prefix}{int(time.time())}_{base}")
    open(marker, "w").close()

    if is_prd:
        print(f"[PRD-DRIFT] PRD modified: {base}. Requirements may have changed — "
              "run /prd-sync to re-baseline.")
    else:
        print(f"[PRD-DRIFT] Implementation file modified: {base}. "
              "Run /prd-sync to verify spec alignment.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - fail open
        raise SystemExit(0)
