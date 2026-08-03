#!/usr/bin/env python3
"""Auto-detecting, non-destructive migration for existing shared-memory databases.

Brings any older `memory` database up to the current schema. Every migration is
(id, description, detect, apply): `detect` inspects the LIVE database and returns
True only when the migration is actually needed, so re-runs are no-ops and the
script self-heals even if the ledger disagrees with reality. Applied migrations
are recorded in `schema_migrations`. Nothing is ever deleted or rewritten —
migrations only add collections, indexes, edge definitions, fields, and
validation rules.

Migrations:
  m001_collections   collections + persistent indexes added since the first release
                     (prd_patches, sync_observations, schema_migrations, search_log)
  m002_graph         memory_graph + all edge definitions, including
                     patch_from_project / observation_from_project
  m003_memory_type   backfill memory_type="pattern" on typeless memories
  m004_edge_weights  backfill co_applied=0 and weight=sim on pattern_relates_to
  m005_validation    attach JSON schema validation (level moderate)
  m006_temporal      backfill bi-temporal validity (valid_from = created_at;
                     superseded docs closed at their superseder's created_at)

Usage (via the MCP server's Poetry env):
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/migrate.py [--dry-run]

Connection resolves env -> arangodb-memory-mcp MCP config -> defaults, exactly
like verify.py. Exit: 0 ok (including nothing-to-do) · 1 failure · 2 no driver.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing; run via the server's Poetry env.\n")
    sys.exit(2)

# Single source of truth for collections/indexes/validation rules.
from setup_schema import COLLECTIONS, INDEXES, SCHEMAS, ensure_collection, ensure_index, ensure_schema  # noqa: E402
from verify import resolve  # noqa: E402  (same three-tier connection resolution)

DRY_RUN = "--dry-run" in sys.argv
GRAPH = "memory_graph"

EDGE_DEFINITIONS = [
    ("pattern_relates_to", ["shared_patterns"], ["shared_patterns"]),
    ("pattern_supersedes", ["shared_patterns"], ["shared_patterns"]),
    ("pattern_from_project", ["shared_patterns"], ["project_registry"]),
    ("alert_from_project", ["drift_alerts"], ["project_registry"]),
    ("pattern_addresses_requirement", ["shared_patterns"], ["drift_alerts"]),
    ("requirement_depends_on", ["drift_alerts"], ["drift_alerts"]),
    ("patch_from_project", ["prd_patches"], ["project_registry"]),
    ("observation_from_project", ["sync_observations"], ["project_registry"]),
]


# --- m001: collections + indexes -------------------------------------------

def detect_collections(db):
    missing_colls = [c for c in COLLECTIONS if not db.has_collection(c)]
    missing_idx = []
    for coll, spec in INDEXES.items():
        if not db.has_collection(coll):
            missing_idx.append(spec["name"])
            continue
        fields = list(spec["fields"])
        if not any(ix.get("type") == "persistent" and list(ix.get("fields", [])) == fields
                   for ix in db.collection(coll).indexes()):
            missing_idx.append(spec["name"])
    return bool(missing_colls or missing_idx)


def apply_collections(db):
    for c in COLLECTIONS:
        ensure_collection(db, c)
    for coll, spec in INDEXES.items():
        ensure_index(db, coll, spec)


# --- m002: graph + edge definitions -----------------------------------------

def detect_graph(db):
    if not db.has_graph(GRAPH):
        return True
    existing = {e["edge_collection"] for e in db.graph(GRAPH).edge_definitions()}
    return any(name not in existing for name, _, _ in EDGE_DEFINITIONS)


def apply_graph(db):
    if not db.has_graph(GRAPH):
        db.create_graph(GRAPH, edge_definitions=[
            {"edge_collection": n, "from_vertex_collections": f, "to_vertex_collections": t}
            for n, f, t in EDGE_DEFINITIONS])
        print(f"  graph {GRAPH!r}: created with {len(EDGE_DEFINITIONS)} edge definitions")
        return
    g = db.graph(GRAPH)
    existing = {e["edge_collection"] for e in g.edge_definitions()}
    for name, frm, to in EDGE_DEFINITIONS:
        if name not in existing:
            g.create_edge_definition(edge_collection=name,
                                     from_vertex_collections=frm,
                                     to_vertex_collections=to)
            print(f"  + edge definition {name!r}")


# --- m003: memory_type backfill ---------------------------------------------

def detect_memory_type(db):
    if not db.has_collection("shared_patterns"):
        return False
    n = next(iter(db.aql.execute(
        "RETURN LENGTH(FOR p IN shared_patterns FILTER p.memory_type == null RETURN 1)")))
    return n > 0


def apply_memory_type(db):
    n = len(list(db.aql.execute("""
        FOR p IN shared_patterns
          FILTER p.memory_type == null
          UPDATE p WITH { memory_type: "pattern" } IN shared_patterns
          RETURN 1""")))
    print(f"  memory_type='pattern' backfilled on {n} document(s)")


# --- m004: edge weight/co_applied backfill -----------------------------------

def detect_edge_weights(db):
    if not db.has_collection("pattern_relates_to"):
        return False
    n = next(iter(db.aql.execute(
        "RETURN LENGTH(FOR e IN pattern_relates_to "
        "FILTER e.weight == null OR e.co_applied == null RETURN 1)")))
    return n > 0


def apply_edge_weights(db):
    n = len(list(db.aql.execute("""
        FOR e IN pattern_relates_to
          FILTER e.weight == null OR e.co_applied == null
          LET co = e.co_applied == null ? 0 : e.co_applied
          LET sim = e.sim == null ? 0 : e.sim
          UPDATE e WITH {
            co_applied: co,
            weight: ROUND((0.7 * sim + 0.3 * (LOG(1 + co) / LOG(11))) * 10000) / 10000
          } IN pattern_relates_to
          RETURN 1""")))
    print(f"  weight/co_applied backfilled on {n} edge(s)")


# --- m005: schema validation --------------------------------------------------

def detect_validation(db):
    for coll, schema in SCHEMAS.items():
        if not db.has_collection(coll):
            continue
        current = (db.collection(coll).properties() or {}).get("schema") or {}
        if current.get("rule") != schema["rule"] or current.get("level") != schema["level"]:
            return True
    return False


def apply_validation(db):
    for coll, schema in SCHEMAS.items():
        if db.has_collection(coll):
            ensure_schema(db, coll, schema)


# --- m006: bi-temporal validity --------------------------------------------

def detect_temporal(db):
    if not db.has_collection("shared_patterns"):
        return False
    n = next(iter(db.aql.execute(
        "RETURN LENGTH(FOR p IN shared_patterns FILTER p.valid_from == null RETURN 1)")))
    return n > 0


def apply_temporal(db):
    """Backfill validity intervals: valid_from = created_at (memories were valid
    from the moment they were saved); superseded memories get their interval
    closed at the SUPERSEDER's creation time — the best available record of when
    the old knowledge stopped being current — and invalidated_by from the
    existing superseded_by pointer. Live memories keep valid_to = null."""
    n_open = len(list(db.aql.execute("""
        FOR p IN shared_patterns
          FILTER p.valid_from == null AND p.superseded != true
          UPDATE p WITH { valid_from: p.created_at, valid_to: null } IN shared_patterns
          RETURN 1""")))
    n_closed = len(list(db.aql.execute("""
        FOR p IN shared_patterns
          FILTER p.valid_from == null AND p.superseded == true
          LET succ = p.superseded_by == null ? null
                     : DOCUMENT("shared_patterns", p.superseded_by)
          UPDATE p WITH {
            valid_from: p.created_at,
            valid_to: succ != null ? succ.created_at : p.created_at,
            invalidated_by: p.superseded_by,
            invalidation_reason: p.invalidation_reason != null
                ? p.invalidation_reason : "superseded (pre-temporal backfill)"
          } IN shared_patterns
          RETURN 1""")))
    print(f"  valid_from/valid_to backfilled: {n_open} live + {n_closed} superseded document(s)")


MIGRATIONS = [
    ("m001_collections", "collections + indexes added since the first release",
     detect_collections, apply_collections),
    ("m002_graph", "memory_graph + all edge definitions",
     detect_graph, apply_graph),
    ("m003_memory_type", "backfill memory_type='pattern' on typeless memories",
     detect_memory_type, apply_memory_type),
    ("m004_edge_weights", "backfill co_applied/weight on pattern_relates_to",
     detect_edge_weights, apply_edge_weights),
    ("m005_validation", "attach JSON schema validation (moderate)",
     detect_validation, apply_validation),
    ("m006_temporal", "backfill bi-temporal validity (valid_from/valid_to/invalidated_by)",
     detect_temporal, apply_temporal),
]


def record(db, mig_id, description):
    if not db.has_collection("schema_migrations"):
        db.create_collection("schema_migrations")
    db.collection("schema_migrations").insert({
        "_key": mig_id,
        "description": description,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }, overwrite=True)


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    db = ArangoClient(hosts=hosts).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=resolve("ARANGO_ROOT_USERNAME", "root"),
        password=resolve("ARANGO_ROOT_PASSWORD", ""))
    print(f"Migration target: {hosts}  db={db.name!r}{'  [DRY RUN]' if DRY_RUN else ''}")

    applied, skipped, failed = [], [], []
    for mig_id, description, detect, apply in MIGRATIONS:
        try:
            needed = detect(db)
        except Exception as exc:  # noqa: BLE001 - a broken detect must not abort the rest
            print(f"  {mig_id}: detect failed ({exc}) — treating as needed")
            needed = True
        if not needed:
            skipped.append(mig_id)
            print(f"  {mig_id}: up to date")
            continue
        if DRY_RUN:
            applied.append(mig_id)
            print(f"  {mig_id}: WOULD APPLY — {description}")
            continue
        print(f"  {mig_id}: applying — {description}")
        try:
            apply(db)
            record(db, mig_id, description)
            applied.append(mig_id)
        except Exception as exc:  # noqa: BLE001
            failed.append(mig_id)
            sys.stderr.write(f"  {mig_id}: FAILED — {exc}\n")

    print("\nSummary: "
          + (f"{len(applied)} applied" + (" (dry run)" if DRY_RUN and applied else ""))
          + f", {len(skipped)} already current"
          + (f", {len(failed)} FAILED: {', '.join(failed)}" if failed else ""))
    if not applied and not failed:
        print("Database is fully migrated.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
