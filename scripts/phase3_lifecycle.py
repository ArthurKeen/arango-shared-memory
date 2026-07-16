#!/usr/bin/env python3
"""Phase 3 — memory lifecycle: supersede, TTL pruning, staleness report.

Three hygiene passes over shared memory:

  1. SUPERSEDE — find near-duplicate pattern pairs (cosine >= --sim, default 0.90).
     The newer (by created_at) supersedes the older: create a pattern_supersedes
     edge (new -> old) and DEMOTE the old pattern so it stops crowding results —
     set superseded=true, superseded_by=<new key>, stash importance_original, and
     set importance=1. This uses the EXISTING /pattern-search scoring (importance +
     recency) to sink the old one; no server change / reload needed.

  2. TTL — ensure a TTL index on drift_alerts.closed_at so CLOSED alerts auto-expire
     after --ttl-days (default 90). Open alerts have no closed_at and never expire.
     (Patterns are NEVER auto-deleted — reusable knowledge.)

  3. STALE REPORT — list (never delete) low-value patterns: importance<=3 AND
     usage_count==0 AND older than --stale-days (default 180), for human review.

Idempotent. Usage:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/phase3_lifecycle.py [--dry-run] \
        [--sim 0.90] [--ttl-days 90] [--stale-days 180]
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
GRAPH, SUP = "memory_graph", "pattern_supersedes"
DRY_RUN = "--dry-run" in sys.argv


def _arg(flag, default):
    return float(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


SIM = _arg("--sim", 0.90)
TTL_DAYS = int(_arg("--ttl-days", 90))
STALE_DAYS = int(_arg("--stale-days", 180))


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
    return re.sub(r"[^A-Za-z0-9_-]", "-", f"{a}__{b}")[:250]


def main() -> int:
    db = ArangoClient(hosts=[h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",")]).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=resolve("ARANGO_ROOT_USERNAME", "root"),
        password=resolve("ARANGO_ROOT_PASSWORD", ""))
    print(f"Phase 3 lifecycle — db='{db.name}' sim>={SIM} ttl={TTL_DAYS}d stale={STALE_DAYS}d"
          f"{'  [DRY RUN]' if DRY_RUN else ''}")

    # --- 1. SUPERSEDE near-duplicates ---
    if SUP not in {e["edge_collection"] for e in db.graph(GRAPH).edge_definitions()} and not DRY_RUN:
        db.graph(GRAPH).create_edge_definition(
            edge_collection=SUP, from_vertex_collections=["shared_patterns"],
            to_vertex_collections=["shared_patterns"])
    pats = list(db.aql.execute(
        "FOR p IN shared_patterns FILTER p.embedding != null AND p.superseded != true "
        "RETURN {k: p._key, e: p.embedding, created: p.created_at, imp: p.importance}"))
    knn = ("FOR q IN shared_patterns LET s = APPROX_NEAR_COSINE(q.embedding, @vec) "
           "SORT s DESC LIMIT 4 RETURN {k: q._key, s: s, created: q.created_at}")
    seen, sup_edges = set(), 0
    for p in pats:
        for nb in db.aql.execute(knn, bind_vars={"vec": p["e"]}):
            if nb["k"] == p["k"] or nb["s"] < SIM:
                continue
            pair = tuple(sorted([p["k"], nb["k"]]))
            if pair in seen:
                continue
            seen.add(pair)
            # newer (larger created_at) supersedes older
            new_k, old_k = (p["k"], nb["k"]) if (p["created"] or "") >= (nb["created"] or "") else (nb["k"], p["k"])
            print(f"    supersede: {new_k} -> {old_k} (sim {nb['s']:.3f})")
            if DRY_RUN:
                continue
            db.collection(SUP).insert({"_key": ekey(new_k, old_k),
                                       "_from": f"shared_patterns/{new_k}",
                                       "_to": f"shared_patterns/{old_k}",
                                       "sim": round(nb["s"], 4)}, overwrite=True)
            old = db.collection("shared_patterns").get(old_k)
            db.collection("shared_patterns").update({
                "_key": old_k, "superseded": True, "superseded_by": new_k,
                "importance_original": old.get("importance_original", old.get("importance", 5)),
                "importance": 1})
            sup_edges += 1
    print(f"  supersede: {sup_edges} pair(s) demoted"
          + ("" if sup_edges or DRY_RUN else " — no near-duplicates found"))

    # --- 2. TTL index on closed drift_alerts ---
    coll = db.collection("drift_alerts")
    has_ttl = any(ix.get("type") == "ttl" and ix.get("fields") == ["closed_at"] for ix in coll.indexes())
    secs = TTL_DAYS * 86400
    if has_ttl:
        print(f"  ttl: index on drift_alerts.closed_at already present")
    elif DRY_RUN:
        print(f"  ttl: would create TTL index on drift_alerts.closed_at ({TTL_DAYS}d)")
    else:
        coll.add_index({"type": "ttl", "fields": ["closed_at"], "expireAfter": secs,
                        "name": "ttl_closed_alerts"})
        print(f"  ttl: created TTL index on drift_alerts.closed_at (expire {TTL_DAYS}d after close)")

    # --- 3. STALE pattern report (never deletes) ---
    stale = list(db.aql.execute(
        "FOR p IN shared_patterns "
        "FILTER (p.importance == null ? 5 : p.importance) <= 3 "
        "  AND (p.usage_count == null ? 0 : p.usage_count) == 0 "
        "  AND p.superseded != true "
        "  AND DATE_DIFF(p.created_at, DATE_NOW(), 'd') > @days "
        "SORT p.created_at RETURN {k: p._key, imp: p.importance, created: p.created_at}",
        bind_vars={"days": STALE_DAYS}))
    print(f"  stale report: {len(stale)} low-value candidate(s) for review (NOT deleted)")
    for s in stale:
        print(f"      - {s['k']} (importance={s['imp']}, created={s['created']})")

    print("\nDry run complete — no changes made." if DRY_RUN else "\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
