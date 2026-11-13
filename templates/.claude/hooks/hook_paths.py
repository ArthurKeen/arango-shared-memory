#!/usr/bin/env python3
"""Where a hook finds its project's files, from the main checkout or a git worktree.

Hooks run in the session's working directory. In a linked git worktree that is the
worktree, which lacks everything the installers place but git does not track:
.claude/hooks and .claude/skills (settings.json finds the hooks themselves through the
main checkout) and, in most projects, CLAUDE.md. This module gives the hooks the same
fallback for the agent doc, and keeps the state they write from showing up as
untracked files in a checkout whose .gitignore does not cover it.
"""

from __future__ import annotations

import os
import subprocess

AGENT_DOCS = ("AGENTS.md", "CLAUDE.md")

#: Written into every directory of local hook state. ``*`` matches the file itself
#: too, so git sees nothing in the directory, whatever the project's .gitignore says.
#: Ten of 23 bootstrapped projects did not ignore a queue directory in the .gitignore
#: they commit (2026-11-06), and a worktree only has the committed one.
PRIVATE_DIR_GITIGNORE = "# Local state of the shared-memory hooks; never committed.\n*\n"


def main_checkout():
    """Root of the main working tree of the repository at the cwd, or None.

    ``git worktree list`` names the main working tree first, from the main checkout,
    a linked worktree or a submodule alike. None when the cwd is not in a git
    repository or git is unavailable, so callers simply get no fallback.
    """
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=5, check=False,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    first = out.stdout.splitlines()[:1]
    if first and first[0].startswith("worktree "):
        return first[0][len("worktree "):]
    return None


def project_root():
    """The one directory all hook state belongs under, for this session.

    CLAUDE_PROJECT_DIR first: Claude Code sets it for every hook command and it is
    stable no matter where the shell has wandered. Then the main checkout, which keeps
    a linked worktree writing to the same place as the checkout it was made from.
    Then the cwd, so a project outside git still works.

    This exists because the apply-gate state used to be addressed relative to the cwd,
    so a session that moved into a worktree split its state across two files: surfaced
    keys in one, applied keys in the other. The gate read whichever was under the cwd
    at stop time and then either blocked forever on keys that HAD been applied, or let
    the session end with keys unresolved. Hit in a real r2g session on 2026-11-12, and
    the only way out was merging the two files by hand -- calling pattern-applied
    again would have double-counted the reuse.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return env
    return main_checkout() or os.getcwd()


def state_dir(kind=".claude"):
    """``<project root>/<kind>/.shared-memory-sessions``.

    Every reader and writer of the apply-gate state must call this and nothing else.
    Five places addressed the directory independently, which is how the writers and
    the gate came to disagree.
    """
    return os.path.join(project_root(), kind, ".shared-memory-sessions")


def legacy_state_dirs(kind=".claude"):
    """Directories a previous version may have written state into, for migration.

    The cwd and the main checkout, when either differs from the resolved root. State
    written before this change still lives there, and a session that straddled a
    worktree has half of itself in each.
    """
    root = os.path.realpath(project_root())
    seen = {root}
    for base in (os.getcwd(), main_checkout()):
        if not base:
            continue
        real = os.path.realpath(base)
        if real in seen:
            continue
        seen.add(real)
        yield os.path.join(base, kind, ".shared-memory-sessions")


def merge_legacy_state(session_id, kind=".claude"):
    """Fold any legacy state for ``session_id`` into the resolved location.

    Union of the key lists, because the split is exactly that: surfaced keys in one
    file and applied keys in the other. Union is also what makes this safe to run on
    every read -- it is idempotent, and it can only ever RESOLVE keys, never
    resurrect one that was already dealt with.

    The legacy file is left in place. Deleting state on behalf of a session that may
    still be running is not worth the saving, and an operator can see what was merged.
    """
    import json

    target = os.path.join(state_dir(kind), f"{session_id}.json")
    merged, found = {}, False
    for legacy_dir in legacy_state_dirs(kind):
        path = os.path.join(legacy_dir, f"{session_id}.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        found = True
        for key, value in data.items():
            if isinstance(value, list):
                merged[key] = sorted(set(merged.get(key, [])) | set(value))
            else:
                merged.setdefault(key, value)
    if not found:
        return False
    try:
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as fh:
                current = json.load(fh)
            for key, value in current.items():
                if isinstance(value, list):
                    merged[key] = sorted(set(merged.get(key, [])) | set(value))
                else:
                    merged[key] = value
        ensure_private_dir(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
    except (OSError, ValueError):
        return False
    return True


def agent_doc_paths():
    """AGENTS.md and CLAUDE.md in the cwd, then in the main checkout when that differs.

    A generator, so a caller that finds its value in the cwd never runs git. The
    main checkout comes second because a branch may change its own AGENTS.md.
    """
    yield from AGENT_DOCS
    root = main_checkout()
    if root and os.path.realpath(root) != os.path.realpath(os.getcwd()):
        for name in AGENT_DOCS:
            yield os.path.join(root, name)


def ensure_private_dir(path):
    """``os.makedirs(path, exist_ok=True)`` that also keeps the directory out of git.

    Dotfile on purpose: every reader of these directories skips dotfiles (markers,
    session files and candidates never start with a dot). A failure to write the
    .gitignore is ignored: the directory is what the caller needs, and a hook must
    not fail because a checkout is read-only.
    """
    os.makedirs(path, exist_ok=True)
    marker = os.path.join(path, ".gitignore")
    if os.path.exists(marker):
        return
    try:
        with open(marker, "x", encoding="utf-8") as fh:
            fh.write(PRIVATE_DIR_GITIGNORE)
    except OSError:  # FileExistsError from a concurrent hook, or a read-only tree
        pass
