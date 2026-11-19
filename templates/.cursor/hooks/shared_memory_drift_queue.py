#!/usr/bin/env python3
"""Cursor afterFileEdit adapter for the canonical PRD drift queue hook."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys


#: The MCP server whose presence in a user-level config means "configured here".
SERVER_ID = "arangodb-memory-mcp"


def shared_memory_configured() -> bool:
    """False when nobody at this machine opted into shared memory (issue #19).

    These hooks run for whoever opens the editor in a bootstrapped project, so an
    unconfigured teammate got a digest that announced its own failure every session
    and a stop gate they had no way to clear. Two doors out: ``SHARED_MEMORY_HOOKS=0``
    for someone who IS configured and wants quiet, and silence for someone who never
    configured the server at all.

    Parsed rather than grepped, so the server's name appearing in a permission
    allowlist or in stored conversation text cannot count as configuration. This
    mirrors the shell guard in front of every Claude hook command
    (templates/.claude/settings.json) and is duplicated into each Cursor hook on
    purpose: a Cursor hook must not depend on the Claude runtime's files.
    """
    if os.environ.get("SHARED_MEMORY_HOOKS", "1") == "0":
        return False
    if os.environ.get("ARANGO_HOSTS"):
        return True
    for name in ("~/.claude.json", "~/.cursor/mcp.json"):
        try:
            with open(os.path.expanduser(name), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        scopes = [doc]
        projects = doc.get("projects")
        if isinstance(projects, dict):
            scopes.extend(v for v in projects.values() if isinstance(v, dict))
        for scope in scopes:
            servers = scope.get("mcpServers")
            if isinstance(servers, dict) and SERVER_ID in servers:
                return True
    return False


def main() -> int:
    if not shared_memory_configured():
        print("{}")
        return 0

    try:
        payload = json.load(sys.stdin)
        file_path = payload.get("file_path") or ""
    except Exception:  # noqa: BLE001
        file_path = ""

    if file_path:
        # Best-effort: a hook must never fail the tool call it observes.
        with contextlib.suppress(Exception):
            subprocess.run(
                [sys.executable, ".claude/hooks/drift_queue.py"],
                input=json.dumps({"tool_input": {"file_path": file_path}}),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    # afterFileEdit has no supported output fields; still emit valid JSON.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
