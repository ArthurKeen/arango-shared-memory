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
COLLECTIONS = ["shared_patterns", "project_registry", "drift_alerts", "search_log"]

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
