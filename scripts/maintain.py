#!/usr/bin/env python3
"""Single maintenance entry point — run all periodic passes in order.

Sequence (each step is idempotent; the whole run is safe to repeat):
  1. phase1b_setup.py       embedding backfill (missing + embedding_pending docs)
                            — skipped when no OPENAI_API_KEY resolves
  2. phase2_setup.py        graph rebuild: similarity + provenance edges
  3. edge-weight pass       weight = 0.7*sim + 0.3*log(1+co_applied)/log(11)
                            (inline AQL; folds observed co-application into ranking)
  4. patch/observation provenance
                            patch_from_project + observation_from_project edges,
                            auto-creating registry nodes so nothing is orphaned
  5. phase3_lifecycle.py    supersede near-duplicates / TTL / stale report
  6. phase2b_extract.py     LLM-extracted edges — ONLY with --with-llm (costs money)
  7. verify.py              health + adoption scorecard (skip with --skip-verify)

Usage (via the MCP server's Poetry env):
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/maintain.py \
        [--dry-run] [--with-llm] [--skip-verify]

Schedule it with scripts/install_maintenance_schedule.sh (launchd on macOS,
prints the crontab line elsewhere). Exit 0 = every executed step succeeded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ID = "arangodb-memory-mcp"
DRY_RUN = "--dry-run" in sys.argv
WITH_LLM = "--with-llm" in sys.argv
SKIP_VERIFY = "--skip-verify" in sys.argv

GRAPH = "memory_graph"
PATCHP, OBSP = "patch_from_project", "observation_from_project"


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
    print(f"\n{'=' * 64}\n▶ {script} {' '.join(args)}\n{'=' * 64}")
    return subprocess.call([sys.executable, path, *args], env=_child_env())


def connect():
    from arango import ArangoClient
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    return ArangoClient(hosts=hosts).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=resolve("ARANGO_ROOT_USERNAME", "root"),
        password=resolve("ARANGO_ROOT_PASSWORD", ""))


def edge_weights(db) -> None:
    """Fold observed co-application into the relates-edge weight."""
    print(f"\n{'=' * 64}\n▶ edge-weight pass (inline)\n{'=' * 64}")
    if not db.has_collection("pattern_relates_to"):
        print("  pattern_relates_to absent — graph layer not installed; skipped")
        return
    q = """
    FOR e IN pattern_relates_to
      LET co = e.co_applied == null ? 0 : e.co_applied
      LET sim = e.sim == null ? 0 : e.sim
      LET w = ROUND((0.7 * sim + 0.3 * (LOG(1 + co) / LOG(11))) * 10000) / 10000
      FILTER e.weight != w OR e.co_applied == null
      UPDATE e WITH { weight: w, co_applied: co } IN pattern_relates_to
      RETURN 1
    """
    if DRY_RUN:
        n = next(iter(db.aql.execute("RETURN LENGTH(pattern_relates_to)")))
        print(f"  would recompute weight on up to {n} edge(s)")
        return
    n = len(list(db.aql.execute(q)))
    print(f"  recomputed weight on {n} edge(s)")


def patch_obs_provenance(db) -> None:
    """Link prd_patches / sync_observations to their project nodes.

    Each collection is handled independently: a missing one is skipped without
    starving the sibling that IS present (older installs may have only one).
    """
    print(f"\n{'=' * 64}\n▶ patch/observation provenance (inline)\n{'=' * 64}")
    present = []
    for edge, coll in ((PATCHP, "prd_patches"), (OBSP, "sync_observations")):
        if db.has_collection(coll):
            present.append((edge, coll))
        else:
            print(f"  {coll} absent — run scripts/migrate.py; skipped")
    if not present:
        return
    if not db.has_graph(GRAPH):
        print(f"  graph {GRAPH!r} absent (no embeddings yet?) — skipped")
        return
    g = db.graph(GRAPH)
    existing = {e["edge_collection"] for e in g.edge_definitions()}
    for edge, coll in present:
        if edge not in existing:
            if DRY_RUN:
                print(f"  would add edge definition {edge!r}")
                continue
            g.create_edge_definition(edge_collection=edge,
                                     from_vertex_collections=[coll],
                                     to_vertex_collections=["project_registry"])
            print(f"  + edge definition {edge!r}")
    if DRY_RUN:
        print("  would upsert registry nodes + rebuild provenance edges")
        return
    # De-orphan: registry node for every project_id seen on a PRESENT patch/observation.
    arrays = [f"(FOR x IN {coll} RETURN x.project_id)" for _, coll in present]
    union_expr = arrays[0] if len(arrays) == 1 else f"APPEND({arrays[0]}, {arrays[1]})"
    db.aql.execute(f"""
      FOR pid IN UNIQUE({union_expr})
        FILTER pid != null
        UPSERT {{ _key: pid }}
        INSERT {{ _key: pid, project_id: pid, project_name: pid, project_type: "other",
                 open_gaps: 0, patterns_contributed: 0, last_sync: null, autocreated: true }}
        UPDATE {{ }} IN project_registry
    """)
    for edge, coll in present:
        n = len(list(db.aql.execute(f"""
          FOR x IN {coll}
            FILTER x.project_id != null
            LET k = LEFT(REGEX_REPLACE(CONCAT(x._key, "__", x.project_id),
                                       "[^A-Za-z0-9_-]", "-"), 250)
            INSERT {{ _key: k, _from: CONCAT("{coll}/", x._key),
                      _to: CONCAT("project_registry/", x.project_id) }}
            INTO {edge} OPTIONS {{ overwriteMode: "replace" }}
            RETURN 1
        """)))
        print(f"  {edge}: {n} provenance edge(s)")


def main() -> int:
    try:
        from arango import ArangoClient  # noqa: F401
    except ModuleNotFoundError:
        sys.stderr.write("error: python-arango missing — run via the server env "
                         "(cd ~/code/arango-solutions-mcp-server && poetry run python ...).\n")
        return 2

    print(f"Maintenance target: {resolve('ARANGO_HOSTS', 'http://localhost:8539')}  "
          f"db={resolve('ARANGO_DEFAULT_DB_NAME', 'memory')!r}"
          f"{'  [DRY RUN]' if DRY_RUN else ''}{'  [+LLM]' if WITH_LLM else ''}")
    passthru = (["--dry-run"] if DRY_RUN else [])
    failed = []

    if resolve("OPENAI_API_KEY"):
        if run("phase1b_setup.py", *passthru) != 0:
            failed.append("phase1b_setup.py")
        if run("phase2_setup.py", *passthru) != 0:
            failed.append("phase2_setup.py")
    else:
        print("\n(no OPENAI_API_KEY resolved — skipping embedding backfill + graph rebuild)")

    db = connect()
    try:
        edge_weights(db)
        patch_obs_provenance(db)
    except Exception as exc:  # noqa: BLE001 - a failed inline pass must not hide the rest
        sys.stderr.write(f"inline pass failed: {exc}\n")
        failed.append("inline-passes")

    if run("phase3_lifecycle.py", *passthru) != 0:
        failed.append("phase3_lifecycle.py")

    if WITH_LLM:
        if run("phase2b_extract.py", *passthru) != 0:
            failed.append("phase2b_extract.py")

    if not SKIP_VERIFY:
        if run("verify.py") != 0:
            failed.append("verify.py")

    print(f"\n{'=' * 64}")
    if failed:
        print(f"MAINTENANCE FINISHED WITH FAILURES: {', '.join(failed)}")
        return 1
    print("MAINTENANCE COMPLETE — all executed steps succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
