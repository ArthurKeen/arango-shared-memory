#!/usr/bin/env python3
"""Phase 1a setup — hybrid-ready BM25 retrieval + graded scoring for shared memory.

Idempotent. Does three things to the 'memory' database:
  1. Adds graded-scoring fields to existing shared_patterns docs:
       importance (int 1-10, default 5), usage_count (int, default 0),
       last_used (ISO, default = created_at). Leaves `worked` untouched.
  2. Creates a `patterns_search` ArangoSearch view over
       problem_description + solution_summary + tags using the built-in
       `text_en` analyzer (stemming + frequency + norm) for BM25 relevance.
  3. Prints a readiness check for Phase 1b (vector index).

Vector/ANN search is intentionally NOT set up here: it requires arangod started
with `--experimental-vector-index` AND an embedding source. See
docs/phase1-implementation.md ("Phase 1b prerequisites").

Connection resolution matches verify.py (env vars -> mcp.json -> defaults).

Usage:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/phase1_setup.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing; run via the server's Poetry env.\n")
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"
VIEW_NAME = "patterns_search"
ANALYZER = "text_en"  # built-in; no custom analyzer needed for English
TEXT_FIELDS = ["problem_description", "solution_summary", "tags"]
DRY_RUN = "--dry-run" in sys.argv


def _from_mcp_config(key: str):
    for path in ["~/.cursor/mcp.json", "~/.claude.json"]:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                env = json.load(f)["mcpServers"][SERVER_ID]["env"]
            if key in env:
                return env[key]
        except (KeyError, json.JSONDecodeError, OSError):
            continue
    return None


def resolve(key: str, default: str) -> str:
    return os.environ.get(key) or _from_mcp_config(key) or default


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    username = resolve("ARANGO_ROOT_USERNAME", "root")
    password = resolve("ARANGO_ROOT_PASSWORD", "")
    db_name = resolve("ARANGO_DEFAULT_DB_NAME", "memory")

    print(f"Phase 1a setup — {hosts} db={db_name!r}{'  [DRY RUN]' if DRY_RUN else ''}")
    client = ArangoClient(hosts=hosts)
    db = client.db(db_name, username=username, password=password)

    if not db.has_collection("shared_patterns"):
        sys.stderr.write("error: shared_patterns missing — run setup_schema.py first.\n")
        return 1

    # 1. Backfill graded-scoring fields (only where absent).
    backfill = """
    FOR p IN shared_patterns
      FILTER p.importance == null OR p.usage_count == null OR p.last_used == null
      UPDATE p WITH {
        importance: p.importance == null ? 5 : p.importance,
        usage_count: p.usage_count == null ? 0 : p.usage_count,
        last_used: p.last_used == null ? (p.created_at == null ? DATE_ISO8601(DATE_NOW()) : p.created_at) : p.last_used
      } IN shared_patterns
      RETURN 1
    """
    if DRY_RUN:
        print("  would backfill importance/usage_count/last_used on shared_patterns")
    else:
        n = len(list(db.aql.execute(backfill)))
        print(f"  backfilled {n} shared_patterns doc(s) with graded-scoring fields")

    # 2. Create/ensure the ArangoSearch view.
    link = {
        "analyzers": [ANALYZER],
        "includeAllFields": False,
        "storeValues": "id",
        "fields": {f: {"analyzers": [ANALYZER]} for f in TEXT_FIELDS},
    }
    existing = [v["name"] for v in db.views()]
    if VIEW_NAME in existing:
        print(f"  view {VIEW_NAME!r}: already exists (updating links)")
        if not DRY_RUN:
            db.update_view(VIEW_NAME, {"links": {"shared_patterns": link}})
    else:
        if DRY_RUN:
            print(f"  would create arangosearch view {VIEW_NAME!r} over {TEXT_FIELDS}")
        else:
            db.create_arangosearch_view(VIEW_NAME, {"links": {"shared_patterns": link}})
            print(f"  view {VIEW_NAME!r}: created over {TEXT_FIELDS}")

    # 3. Phase 1b readiness probe (non-fatal).
    print("\nPhase 1b (vector) readiness:")
    print("  - vector index requires arangod --experimental-vector-index (currently DISABLED)")
    print("  - embedding source required (MCP server does not generate embeddings)")
    print("  see docs/phase1-implementation.md")

    print("\nDone." if not DRY_RUN else "\nDry run complete — no changes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
