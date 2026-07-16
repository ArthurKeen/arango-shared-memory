#!/usr/bin/env python3
"""Phase 2 setup — graph layer over shared memory.

Creates a named graph `memory_graph` with edge collections and populates the
similarity edges that power graph-expanded retrieval:

  - pattern_relates_to   shared_patterns <-> shared_patterns  (semantic KNN links)
  - pattern_supersedes   shared_patterns  -> shared_patterns  (reserved; populated
                          when a newer pattern contradicts an older one — Phase 3)
  - pattern_from_project  shared_patterns -> project_registry (provenance, if the
                          project is registered)

`pattern_relates_to` is built from the existing embeddings via ANN KNN (no LLM,
no new API calls): for each pattern, link its top-K nearest neighbours above a
cosine threshold. Deterministic edge _keys make re-runs idempotent.

Prereq: phase1b_setup.py has run (embeddings + vector index exist).

Usage:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/phase2_setup.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import re
import sys

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing; run via the server's Poetry env.\n")
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"
GRAPH = "memory_graph"
REL, SUP, FROMP = "pattern_relates_to", "pattern_supersedes", "pattern_from_project"
TOP_K = 3          # neighbours per pattern
MIN_SIM = 0.30     # cosine threshold to create an edge
DRY_RUN = "--dry-run" in sys.argv


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


def ekey(a, b):
    """Deterministic, order-preserving edge key from two document keys."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", f"{a}__{b}")[:250]


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    db = ArangoClient(hosts=hosts).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=resolve("ARANGO_ROOT_USERNAME", "root"),
        password=resolve("ARANGO_ROOT_PASSWORD", ""))
    print(f"Phase 2 setup — {hosts} db='{db.name}'{'  [DRY RUN]' if DRY_RUN else ''}")

    # 1. Named graph + edge collections (idempotent).
    edge_defs = [
        {"edge_collection": REL, "from_vertex_collections": ["shared_patterns"],
         "to_vertex_collections": ["shared_patterns"]},
        {"edge_collection": SUP, "from_vertex_collections": ["shared_patterns"],
         "to_vertex_collections": ["shared_patterns"]},
        {"edge_collection": FROMP, "from_vertex_collections": ["shared_patterns"],
         "to_vertex_collections": ["project_registry"]},
    ]
    if db.has_graph(GRAPH):
        print(f"  graph {GRAPH!r}: already exists")
        g = db.graph(GRAPH)
        existing = {e["edge_collection"] for e in g.edge_definitions()}
        for ed in edge_defs:
            if ed["edge_collection"] not in existing and not DRY_RUN:
                g.create_edge_definition(**ed)
                print(f"    + edge definition {ed['edge_collection']!r}")
    elif DRY_RUN:
        print(f"  would create graph {GRAPH!r} with edges {REL}, {SUP}, {FROMP}")
    else:
        db.create_graph(GRAPH, edge_definitions=edge_defs)
        print(f"  graph {GRAPH!r}: created with {REL}, {SUP}, {FROMP}")

    if DRY_RUN:
        pats = list(db.aql.execute(
            "RETURN LENGTH(FOR p IN shared_patterns FILTER p.embedding != null RETURN 1)"))[0]
        print(f"  would build ~{REL} edges for {pats} embedded pattern(s) (top {TOP_K}, sim>={MIN_SIM})")
        print("\nDry run complete — no changes made.")
        return 0

    rel = db.collection(REL)
    fromp = db.collection(FROMP)

    # 2. pattern_relates_to via ANN KNN over stored embeddings.
    patterns = list(db.aql.execute(
        "FOR p IN shared_patterns FILTER p.embedding != null RETURN {k: p._key, e: p.embedding}"))
    # APPROX_NEAR_COSINE must appear once, bound via LET and used in SORT; a second
    # direct call (e.g. in RETURN) breaks the vector-index optimizer (ERR 1554).
    knn = """
    FOR q IN shared_patterns
      LET s = APPROX_NEAR_COSINE(q.embedding, @vec)
      SORT s DESC
      LIMIT @lim
      RETURN {k: q._key, s: s}
    """
    edges = 0
    for p in patterns:
        for nb in db.aql.execute(knn, bind_vars={"vec": p["e"], "lim": TOP_K + 1}):
            if nb["k"] == p["k"] or nb["s"] < MIN_SIM:
                continue
            rel.insert({"_key": ekey(p["k"], nb["k"]),
                        "_from": f"shared_patterns/{p['k']}",
                        "_to": f"shared_patterns/{nb['k']}",
                        "sim": round(nb["s"], 4)}, overwrite=True)
            edges += 1
    print(f"  {REL}: {edges} edge(s) over {len(patterns)} pattern(s) (top {TOP_K}, sim>={MIN_SIM})")

    # 3. pattern_from_project provenance (only where the project is registered).
    prov = 0
    for p in db.aql.execute("FOR p IN shared_patterns RETURN {k: p._key, pid: p.project_id}"):
        if p["pid"] and db.collection("project_registry").has(p["pid"]):
            fromp.insert({"_key": ekey(p["k"], p["pid"]),
                          "_from": f"shared_patterns/{p['k']}",
                          "_to": f"project_registry/{p['pid']}"}, overwrite=True)
            prov += 1
    print(f"  {FROMP}: {prov} provenance edge(s)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
