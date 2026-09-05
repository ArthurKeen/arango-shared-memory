#!/usr/bin/env python3
"""Stop-hook reconciliation — queue drift for edits the PostToolUse hook cannot see.

`drift_queue.py` fires on Write|Edit|MultiEdit|NotebookEdit and reads
`tool_input.file_path`. A Bash call carries no `file_path`, so `cat > f <<EOF`,
`sed -i`, a generated script, or an edit made outside the session entirely never
queues a marker — and the Stop gate, which only counts markers, then lets the
session end with unaudited changes. That is not an edge case: tool-preference
settings actively steer agents toward the shell, so it is the common path.

This closes the gap from the other side. Rather than guessing which files a shell
command touched — unknowable without parsing arbitrary shell — it asks git what
actually changed and queues whatever the queue is missing. Every write path is
covered, because none of them can hide from the working tree.

Scope is "changed since the last /prd-sync":

    uncommitted   git status --porcelain
    committed     git diff --name-only <last-sync SHA>..HEAD

The SHA lives in `.prd-drift-queue/.last-sync`, written by /prd-sync Phase 6c. It
is a **dotfile on purpose**: `rm -f .prd-drift-queue/*` does not match it and `ls`
does not count it, so the existing clear step and the existing gate arithmetic
both keep working untouched. With no stamp — the first run after adopting this
hook — only uncommitted changes count, so adoption cannot flood the queue with a
repository's entire history.

Fails open in every direction: not a git repo, git missing, unreadable stamp, any
exception at all -> queue nothing, exit 0. A broken reconciler must never trap a
session, and must never manufacture drift that isn't there.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from drift_queue import classify, enqueue, prd_file_from_claude_md  # noqa: E402

STAMP = os.path.join(".prd-drift-queue", ".last-sync")


def _git(*args, timeout=5):
    """Run a git command, returning stdout or None. Never raises."""
    try:
        out = subprocess.run(("git",) + args, capture_output=True, text=True,
                             timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def last_sync_sha():
    """The commit /prd-sync last audited, or None.

    A stamp naming a commit that no longer exists (rebase, amend, a reset that
    dropped it) is treated as absent rather than as an error: resolving a range
    against a dead SHA would make git fail and silently reconcile nothing, which
    is the failure this hook exists to prevent.
    """
    try:
        with open(STAMP, encoding="utf-8") as fh:
            sha = fh.read().split()[0].strip()
    except (OSError, IndexError):
        return None
    if not sha:
        return None
    return sha if _git("cat-file", "-e", sha + "^{commit}") is not None else None


def changed_paths():
    """Every path touched since the last sync — uncommitted plus committed."""
    paths = set()

    # Uncommitted. --porcelain columns are fixed-width status + path; a rename
    # reads "R  old -> new" and only the destination is the edited file.
    status = _git("status", "--porcelain", "--untracked-files=all")
    if status is None:
        return paths          # not a git repo (or git unavailable) -> fail open
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path.strip().strip('"'))

    # Committed since the stamp. Without a stamp this half is skipped entirely.
    sha = last_sync_sha()
    if sha:
        diff = _git("diff", "--name-only", f"{sha}..HEAD")
        if diff:
            paths.update(p.strip() for p in diff.splitlines() if p.strip())

    return paths


def main() -> int:
    if os.path.exists(".no-drift-gate"):
        return 0

    prd = prd_file_from_claude_md()
    queued = []
    for path in sorted(changed_paths()):
        if not os.path.exists(path):
            continue                      # deleted, or a path git reports we can't see
        kind = classify(path, prd=prd)
        if kind is None:
            continue
        if enqueue(path, kind) is not None:
            queued.append((kind, path))

    if queued:
        # Named, not just counted: the whole point is that these edits were
        # invisible, so the message has to say which ones were recovered.
        shown = ", ".join(p for _, p in queued[:8])
        more = f" (+{len(queued) - 8} more)" if len(queued) > 8 else ""
        print(f"[PRD-DRIFT] Reconciled {len(queued)} change(s) the edit hook did not "
              f"see (shell/script/external edits): {shown}{more}. Run /prd-sync.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - fail open
        raise SystemExit(0)
