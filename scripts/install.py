#!/usr/bin/env python3
"""One-shot installer for the shared-memory backend (run once per ArangoDB target).

Runs the setup sequence in the correct order and reports readiness:
  1. setup_schema.py   — memory db + collections (shared_patterns, project_registry,
                         drift_alerts, search_log) + indexes
  2. phase1_setup.py   — patterns_search ArangoSearch view + graded-scoring fields
                         (REQUIRED — the pattern-search tool's BM25 path needs this view)
  3. verify.py         — health check + adoption/read-path scorecard

Hybrid (vector) + graph retrieval activate later, once you have an OpenAI key and
at least one saved pattern:
  - set OPENAI_API_KEY (+ EMBEDDING_MODEL) in the MCP env, save a pattern, then run
      phase1b_setup.py   (embeddings + cosine vector index)
      phase2_setup.py    (graph: pattern_relates_to / from_project edges)
  - phase2b_extract.py / phase3_lifecycle.py are periodic maintenance (LLM edges, dedup/TTL).
This installer runs the always-safe prefix (1–3); it will run phase1b/phase2 too if
--with-embeddings is passed AND an OpenAI key is resolvable AND patterns already exist.

Connection is resolved exactly like verify.py (env vars → arangodb-memory-mcp entry in
~/.cursor/mcp.json or ~/.claude.json → defaults). Run via the server's env, e.g.:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/install.py [--with-embeddings]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ID = "arangodb-memory-mcp"
WITH_EMB = "--with-embeddings" in sys.argv


def _from_mcp_config(key):
    for path in ["~/.cursor/mcp.json", "~/.claude.json"]:
        p = os.path.expanduser(path)
        if os.path.exists(p):
            try:
                env = json.load(open(p))["mcpServers"][SERVER_ID]["env"]
                if key in env:
                    return env[key]
            except (KeyError, json.JSONDecodeError, OSError):
                pass
    return None


def resolve(key, default=""):
    return os.environ.get(key) or _from_mcp_config(key) or default


def _child_env():
    """Resolve the connection ONCE and inject it, so subprocesses that read plain
    env vars with different defaults (e.g. setup_schema.py defaults to 8529/_system)
    all target the same resolved host/db instead of falling back to 401."""
    env = dict(os.environ)
    for k, default in (("ARANGO_HOSTS", "http://localhost:8539"),
                       ("ARANGO_ROOT_USERNAME", "root"),
                       ("ARANGO_ROOT_PASSWORD", ""),
                       ("ARANGO_DEFAULT_DB_NAME", "memory"),
                       ("OPENAI_API_KEY", ""),
                       ("EMBEDDING_MODEL", "")):
        v = resolve(k, default)
        if v:
            env[k] = v
    return env


def run(script, *args) -> int:
    path = os.path.join(HERE, script)
    print(f"\n{'='*64}\n▶ {script} {' '.join(args)}\n{'='*64}")
    return subprocess.call([sys.executable, path, *args], env=_child_env())


def main() -> int:
    try:
        from arango import ArangoClient  # noqa: F401
    except ModuleNotFoundError:
        sys.stderr.write("error: python-arango missing — run via the server env "
                         "(cd ~/code/arango-solutions-mcp-server && poetry run python ...).\n")
        return 2

    hosts = resolve("ARANGO_HOSTS", "http://localhost:8539")
    print(f"Installer target: {hosts}  db={resolve('ARANGO_DEFAULT_DB_NAME','memory')!r}")

    for script in ("setup_schema.py", "phase1_setup.py"):
        rc = run(script)
        if rc != 0:
            sys.stderr.write(f"\n{script} failed (exit {rc}) — stopping.\n")
            return rc

    have_key = bool(resolve("OPENAI_API_KEY"))
    if WITH_EMB and have_key:
        run("phase1b_setup.py")   # no-op (defers index) if 0 patterns yet
        run("phase2_setup.py")
    elif WITH_EMB and not have_key:
        print("\n--with-embeddings requested but no OPENAI_API_KEY resolved — skipping "
              "phase1b/phase2. Add the key to the MCP env and re-run with --with-embeddings.")

    run("verify.py")

    print(f"\n{'='*64}\nNEXT STEPS")
    print("- Register the MCP server globally (setup.md STEP 3) if not done.")
    print("- Bootstrap projects: scripts/bootstrap_project.sh (installs current skills/hooks).")
    if not have_key:
        print("- For hybrid+graph: add OPENAI_API_KEY + EMBEDDING_MODEL to the MCP env, save a")
        print("  pattern, then run phase1b_setup.py + phase2_setup.py (or re-run with --with-embeddings).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
