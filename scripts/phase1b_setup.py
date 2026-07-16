#!/usr/bin/env python3
"""Phase 1b setup — embeddings + vector index for shared memory (OpenAI).

Prerequisites (both must already be true):
  1. arangod running with --experimental-vector-index (see docs/phase1-implementation.md).
  2. OPENAI_API_KEY available (env var, or in the arangodb-memory-mcp env of
     ~/.cursor/mcp.json / ~/.claude.json).

Does, idempotently, against the 'memory' database:
  1. Backfills `embedding` on every shared_patterns doc missing one, embedding
     "<problem_description>\n<solution_summary>" via OpenAI (text-embedding-3-small,
     1536 dims by default; override with EMBEDDING_MODEL).
  2. Creates a cosine vector index on shared_patterns.embedding (only once at least
     one embedded doc exists — ArangoDB requires vector data before index creation).

Usage:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/phase1b_setup.py [--dry-run]

Exit: 0 ok · 1 config/connection failure · 2 python-arango missing.
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

import urllib.error
import urllib.request

SERVER_ID = "arangodb-memory-mcp"
OPENAI_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_MODEL = "text-embedding-3-small"
DIMENSION = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}
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


def resolve(key: str, default: str = "") -> str:
    return os.environ.get(key) or _from_mcp_config(key) or default


def embed(texts, model, api_key):
    """Return a list of embedding vectors for `texts` via OpenAI."""
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request(
        OPENAI_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    items = sorted(data["data"], key=lambda d: d["index"])
    return [it["embedding"] for it in items]


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    username = resolve("ARANGO_ROOT_USERNAME", "root")
    password = resolve("ARANGO_ROOT_PASSWORD", "")
    db_name = resolve("ARANGO_DEFAULT_DB_NAME", "memory")
    model = resolve("EMBEDDING_MODEL", DEFAULT_MODEL)
    api_key = resolve("OPENAI_API_KEY")
    dim = DIMENSION.get(model, 1536)

    print(f"Phase 1b setup — {hosts} db={db_name!r} model={model!r} dim={dim}"
          f"{'  [DRY RUN]' if DRY_RUN else ''}")
    if not api_key:
        sys.stderr.write("error: OPENAI_API_KEY not found (env or mcp.json). Set it and retry.\n")
        return 1

    db = ArangoClient(hosts=hosts).db(db_name, username=username, password=password)
    if not db.has_collection("shared_patterns"):
        sys.stderr.write("error: shared_patterns missing — run setup_schema.py first.\n")
        return 1
    coll = db.collection("shared_patterns")

    # 1. Backfill embeddings.
    todo = list(db.aql.execute(
        "FOR p IN shared_patterns FILTER p.embedding == null "
        "RETURN {k: p._key, text: CONCAT_SEPARATOR('\n', p.problem_description, p.solution_summary)}"))
    print(f"  {len(todo)} pattern(s) need embeddings")
    if todo and not DRY_RUN:
        # Batch in groups of 100 to bound request size.
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            vectors = embed([b["text"] or "" for b in batch], model, api_key)
            for b, v in zip(batch, vectors):
                coll.update({"_key": b["k"], "embedding": v})
            print(f"    embedded {min(i + 100, len(todo))}/{len(todo)}")
    elif todo:
        print("    would embed and store vectors (dry run)")

    # 2. Create vector index (requires >=1 embedded doc).
    have = next(iter(db.aql.execute(
        "RETURN LENGTH(FOR p IN shared_patterns FILTER p.embedding != null RETURN 1)")))
    idx_present = any(ix.get("type") == "vector" and ix.get("fields") == ["embedding"]
                      for ix in coll.indexes())
    if idx_present:
        print("  vector index on embedding: already present")
    elif have == 0:
        print("  vector index: SKIPPED — no embedded docs yet (created after first pattern is saved)")
    elif DRY_RUN:
        print(f"  would create cosine vector index (dimension {dim}) on shared_patterns.embedding")
    else:
        # nLists partitions vectors into Voronoi cells; APPROX_NEAR_COSINE probes only
        # `defaultNProbe` of them. For a SMALL corpus, >1 cell + nProbe=1 cripples recall
        # (cells become near-singletons → KNN returns almost nothing → orphaned graph nodes,
        # poor ranking). So use a single exhaustive cell until the corpus is large, then
        # switch to the ~15*sqrt(N) heuristic with nProbe covering a fraction of cells.
        if have < 1000:
            n_lists, n_probe = 1, 1
        else:
            n_lists = int(15 * (have ** 0.5))
            n_probe = max(1, n_lists // 8)
        coll.add_index({"type": "vector", "fields": ["embedding"], "name": "emb_cos_idx",
                        "params": {"metric": "cosine", "dimension": dim,
                                   "nLists": n_lists, "defaultNProbe": n_probe}})
        print(f"  vector index created (dimension {dim}, nLists {n_lists}, nProbe {n_probe})")

    print("\nDone." if not DRY_RUN else "\nDry run complete — no changes made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
