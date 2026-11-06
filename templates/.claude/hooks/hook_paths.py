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
