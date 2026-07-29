#!/usr/bin/env python3
"""Idempotent ArangoDB schema setup for the shared workflow-automation system.

Creates three document collections and two persistent indexes:
  - shared_patterns   (idx: problem_category, project_type, created_at)
  - project_registry
  - drift_alerts      (idx: project_id, status, detected_at)

Connection settings are read from the same environment variables the
arango-solutions-mcp-server uses, so a single configuration works for both:

  ARANGO_HOSTS          default: http://localhost:8529  (comma-separated allowed)
  ARANGO_ROOT_USERNAME  default: root
  ARANGO_ROOT_PASSWORD  default: ""  (empty)
  ARANGO_DEFAULT_DB_NAME default: _system
  ARANGO_VERIFY_SSL     default: true   (set "false" to disable verification)

Usage:
    python scripts/setup_schema.py

Exit codes:
    0  schema is ready (created or already present)
    1  connection / configuration failure
    2  python-arango not installed
"""

from __future__ import annotations

import os
import sys

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write(
        "error: python-arango is not installed.\n"
        "Install it (e.g. `pip install python-arango`) or run this from the\n"
        "arango-solutions-mcp-server Poetry environment:\n"
        "    cd ~/code/arango-solutions-mcp-server && poetry run python "
        "~/code/arango-shared-memory/scripts/setup_schema.py\n"
    )
    sys.exit(2)


# search_log: read-path instrumentation — one doc per /pattern-search call
# (query, mode, count, top hit, hit-bool, project). Lets us measure whether shared
# memory is actually being *read*, not just written. Also lazily created by the
# pattern-search tool if absent.
# prd_patches: reverse drift (code -> PRD) proposals with a review state machine.
# sync_observations: audit findings that became neither alert nor accepted patch,
# consumed as hints by the next /prd-sync run.
# schema_migrations: ledger of applied migrations (scripts/migrate.py).
COLLECTIONS = ["shared_patterns", "project_registry", "drift_alerts", "search_log",
               "prd_patches", "sync_observations", "schema_migrations"]

INDEXES = {
    "shared_patterns": {
        "fields": ["problem_category", "project_type", "created_at"],
        "name": "idx_patterns_category",
    },
    "drift_alerts": {
        "fields": ["project_id", "status", "detected_at"],
        "name": "idx_alerts_project",
    },
    "search_log": {
        "fields": ["project_id", "created_at"],
        "name": "idx_search_log_project",
    },
    "prd_patches": {
        "fields": ["project_id", "review_state", "created_at"],
        "name": "idx_patches_project",
    },
    "sync_observations": {
        "fields": ["project_id", "state", "created_at"],
        "name": "idx_observations_project",
    },
}

# Server-side JSON schema validation, level "moderate": new/changed documents are
# validated; documents that already violated the schema remain readable/updatable.
# Rules are deliberately permissive (extra fields always allowed) — they exist to
# stop structural rot (wrong types, missing identity fields, invalid enums), not
# to freeze the document shape. search_log/schema_migrations carry no schema
# (external writer / internal ledger).
MEMORY_TYPES = ["pattern", "feedback", "user", "project", "reference", None]
SCHEMAS = {
    "shared_patterns": {
        "rule": {
            "type": "object",
            "required": ["project_id", "problem_description"],
            "properties": {
                "project_id": {"type": "string"},
                "problem_description": {"type": "string"},
                "memory_type": {"enum": MEMORY_TYPES},
                "importance": {"type": ["number", "null"]},
                "usage_count": {"type": ["number", "null"]},
                "tags": {"type": ["array", "null"]},
                "superseded": {"type": ["boolean", "null"]},
                "embedding": {"type": ["array", "null"]},
                "embedding_pending": {"type": ["boolean", "null"]},
                "why": {"type": ["string", "null"]},
                "how_to_apply": {"type": ["string", "null"]},
            },
        },
        "level": "moderate",
        "message": "shared_patterns: needs string project_id + problem_description; "
                   "memory_type must be pattern|feedback|user|project|reference",
    },
    "project_registry": {
        "rule": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "open_gaps": {"type": ["number", "null"]},
                "patterns_contributed": {"type": ["number", "null"]},
                "prd_sha256": {"type": ["string", "null"]},
            },
        },
        "level": "moderate",
        "message": "project_registry: needs string project_id",
    },
    "drift_alerts": {
        "rule": {
            "type": "object",
            "required": ["project_id", "req_id"],
            "properties": {
                "project_id": {"type": "string"},
                "req_id": {"type": "string"},
                "classification": {"enum": ["MISSING", "PARTIAL", None]},
                "status": {"enum": ["open", "closed", None]},
            },
        },
        "level": "moderate",
        "message": "drift_alerts: needs project_id + req_id; "
                   "classification MISSING|PARTIAL; status open|closed",
    },
    "prd_patches": {
        "rule": {
            "type": "object",
            "required": ["project_id", "delta_type", "review_state"],
            "properties": {
                "project_id": {"type": "string"},
                "delta_type": {"enum": ["missing-semantics", "wrong-signature", "typo",
                                        "obsolete", "clarification", "new-requirement"]},
                "review_state": {"enum": ["proposed", "accepted", "rejected", "superseded"]},
            },
        },
        "level": "moderate",
        "message": "prd_patches: needs project_id, a valid delta_type, and a valid review_state",
    },
    "sync_observations": {
        "rule": {
            "type": "object",
            "required": ["project_id", "observation_type", "state"],
            "properties": {
                "project_id": {"type": "string"},
                "observation_type": {"enum": ["spec_gap", "assumption_violation",
                                              "precision_needed", "edge_case",
                                              "cross_layer_invariant", "design_alternative",
                                              "deprecation_signal"]},
                "state": {"enum": ["unprocessed", "acknowledged", "promoted",
                                   "rejected", "duplicate"]},
                "severity": {"enum": ["low", "medium", "high", None]},
            },
        },
        "level": "moderate",
        "message": "sync_observations: needs project_id, a valid observation_type, "
                   "and a valid state",
    },
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def connect():
    hosts = [
        h.strip()
        for h in os.environ.get("ARANGO_HOSTS", "http://localhost:8529").split(",")
        if h.strip()
    ]
    username = os.environ.get("ARANGO_ROOT_USERNAME", "root")
    password = os.environ.get("ARANGO_ROOT_PASSWORD", "")
    db_name = os.environ.get("ARANGO_DEFAULT_DB_NAME", "_system")
    verify = _env_bool("ARANGO_VERIFY_SSL", True)

    print(f"Connecting to {hosts} as {username!r}, target database {db_name!r} ...")
    client = ArangoClient(hosts=hosts, verify_override=verify)

    # Connect to _system first so we can create the target database if needed.
    sys_db = client.db("_system", username=username, password=password)
    version = sys_db.version()  # authenticated round-trip; fail fast on misconfig
    print(f"Connected. ArangoDB server version: {version}")

    if db_name != "_system":
        if sys_db.has_database(db_name):
            print(f"  database {db_name!r}: already exists")
        else:
            sys_db.create_database(db_name)
            print(f"  database {db_name!r}: created")

    return client.db(db_name, username=username, password=password)


def ensure_collection(db, name: str) -> None:
    if db.has_collection(name):
        print(f"  collection {name!r}: already exists")
    else:
        db.create_collection(name)
        print(f"  collection {name!r}: created")


def ensure_index(db, collection: str, spec: dict) -> None:
    coll = db.collection(collection)
    wanted_fields = list(spec["fields"])
    for existing in coll.indexes():
        if existing.get("type") == "persistent" and list(
            existing.get("fields", [])
        ) == wanted_fields:
            print(
                f"  index {spec['name']!r} on {collection!r}: "
                f"already present ({existing.get('id')})"
            )
            return
    coll.add_index({"type": "persistent", "fields": wanted_fields, "name": spec["name"]})
    print(f"  index {spec['name']!r} on {collection!r}: created")


def ensure_schema(db, collection: str, schema: dict) -> None:
    coll = db.collection(collection)
    current = (coll.properties() or {}).get("schema") or {}
    if current.get("rule") == schema["rule"] and current.get("level") == schema["level"]:
        print(f"  schema validation on {collection!r}: already current")
        return
    coll.configure(schema=schema)
    print(f"  schema validation on {collection!r}: applied (level {schema['level']})")


def main() -> int:
    try:
        db = connect()
    except Exception as exc:  # noqa: BLE001 - surface any connection/auth error
        sys.stderr.write(f"error: could not connect to ArangoDB: {exc}\n")
        return 1

    print("Ensuring collections ...")
    for name in COLLECTIONS:
        ensure_collection(db, name)

    print("Ensuring indexes ...")
    for collection, spec in INDEXES.items():
        ensure_index(db, collection, spec)

    print("Ensuring schema validation ...")
    for collection, schema in SCHEMAS.items():
        ensure_schema(db, collection, schema)

    print("Verifying ...")
    present = [c["name"] for c in db.collections() if not c["name"].startswith("_")]
    missing = [c for c in COLLECTIONS if c not in present]
    if missing:
        sys.stderr.write(f"error: collections still missing: {missing}\n")
        return 1

    print("Schema ready: " + ", ".join(COLLECTIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
