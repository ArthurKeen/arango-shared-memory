#!/usr/bin/env python3
"""PostToolUse hook (Write|Edit|MultiEdit|NotebookEdit) — queue drift markers for /prd-sync.

Reads the hook payload from stdin. Two triggers:
  - an implementation file was edited  -> marker  <epoch>_<basename>
  - the project's PRD file was edited  -> marker  prd_<epoch>_<basename>
    (requirements may have changed, not just code — a distinct signal)

PRD_FILE is parsed at runtime from ./AGENTS.md (canonical) with ./CLAUDE.md as
the legacy fallback, so this hook needs no per-project rendering. Fail-open: any
error exits 0 silently.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

SOURCE_EXT = re.compile(
    r"\.(ts|js|tsx|jsx|py|go|rs|java|cs|cpp|c|rb|php|swift|kt)$", re.IGNORECASE)

#: Top-level directories that are never PRD-tracked code.
#: ``.claude`` and ``.cursor`` hold agent config (hooks/rules/skills) — both ship .py
#: hooks, so an extension test alone would queue drift for the tooling that reports it.
#: ``.prd-drift-queue`` is the queue itself and
#: MUST be excluded or the Stop-time reconciler feeds on its own output: the directory
#: is untracked, so ``git status --untracked-files=all`` lists every marker, and a
#: marker named ``<epoch>_foo.py`` matches SOURCE_EXT and queues a marker named
#: ``<epoch>_<epoch>_foo.py``, which queues another — the queue grows on every Stop.
EXCLUDED_TOP_LEVEL = frozenset({".claude", ".cursor", ".prd-drift-queue", ".git"})


def prd_file_from_claude_md(path=None):
    """Read PRD_FILE from the project's agent-doc — AGENTS.md (canonical) preferred,
    CLAUDE.md the legacy fallback. An explicit ``path`` reads only that file."""
    for p in ([path] if path is not None else ["AGENTS.md", "CLAUDE.md"]):
        try:
            with open(p, encoding="utf-8") as fh:
                m = re.search(r"PRD_FILE:\s*(\S+)", fh.read())
        except OSError:
            continue
        # Strip markdown decoration (`path`, **path**) — a backtick-wrapped value
        # would never match a real edited path, silently disabling PRD-edit markers.
        if m:
            value = m.group(1).strip("`*_\"' ")
            if value and not value.startswith("<"):
                return value
    return None


def classify(file_path, prd=None):
    """Is this path PRD-tracked? -> "prd" | "source" | None.

    Shared with reconcile_drift_queue.py so the Stop-time reconciler and this hook
    can never disagree about what counts as a tracked edit. ``prd`` is passed in
    when a caller classifies many paths at once, to read the agent-doc once.
    """
    # Only queue drift for files inside THIS project. An absolute path pointing at
    # another repo (e.g. a sibling library edited in the same session) must not queue
    # drift here — otherwise the Stop gate fires on unrelated cross-repo edits. Also
    # skip .claude/ internals (hooks/config/skills), which aren't PRD-tracked code.
    # realpath (not abspath) on both sides so a project under a symlinked path
    # (e.g. macOS /tmp -> /private/tmp) still compares correctly.
    repo_root = os.path.realpath(os.getcwd())
    abs_path = os.path.realpath(file_path)
    try:
        inside = os.path.commonpath([repo_root, abs_path]) == repo_root
    except ValueError:  # different drives / relative-vs-absolute mismatch
        inside = False
    if not inside:
        return None
    top = os.path.relpath(abs_path, repo_root).split(os.sep, 1)[0]
    if top in EXCLUDED_TOP_LEVEL:
        return None

    base = os.path.basename(file_path)
    if prd is None:
        prd = prd_file_from_claude_md()
    if prd and (os.path.normpath(file_path).endswith(os.path.normpath(prd))
                or base == os.path.basename(prd)):
        return "prd"
    return "source" if SOURCE_EXT.search(file_path) else None


def enqueue(file_path, kind):
    """Write one marker. Returns its path, or None if an identical one is pending.

    Deduplicating on the trailing ``_<basename>`` keeps a file edited twenty times
    from queuing twenty markers — the gate's message counts changes, and a count
    inflated by repetition misreports how much there is to audit.
    """
    os.makedirs(".prd-drift-queue", exist_ok=True)
    base = os.path.basename(file_path)
    prefix = "prd_" if kind == "prd" else ""
    try:
        for existing in os.listdir(".prd-drift-queue"):
            if existing.startswith(prefix) and existing.endswith("_" + base):
                # A prd_ marker also ends with _<base>, so a bare source marker must
                # not match one; the startswith check above keeps the two namespaces
                # apart in both directions.
                if prefix or not existing.startswith("prd_"):
                    return None
    except OSError:
        pass
    marker = os.path.join(".prd-drift-queue", f"{prefix}{int(time.time())}_{base}")
    open(marker, "w").close()
    return marker


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    tool_input = payload.get("tool_input", payload) or {}
    # Write/Edit/MultiEdit use file_path; NotebookEdit uses notebook_path.
    file_path = (tool_input.get("file_path") or tool_input.get("path")
                 or tool_input.get("notebook_path") or "")
    if not file_path:
        return 0

    kind = classify(file_path)
    if kind is None:
        return 0
    if enqueue(file_path, kind) is None:
        return 0
    base = os.path.basename(file_path)

    if kind == "prd":
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
