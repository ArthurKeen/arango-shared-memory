#!/usr/bin/env python3
"""Health + adoption checker for the shared-memory system.

Verifies the plumbing (database, collections, indexes, read/write round-trip)
and prints an adoption snapshot (pattern count, registered projects, open vs.
closed drift alerts) so you can tell at a glance whether the system is both
*working* and *being used*.

Connection settings are resolved in this order (first wins):
  1. Environment variables: ARANGO_HOSTS, ARANGO_ROOT_USERNAME,
     ARANGO_ROOT_PASSWORD, ARANGO_DEFAULT_DB_NAME
  2. The 'arangodb-memory-mcp' server entry in ~/.cursor/mcp.json
     (or ~/.claude.json) -- so it uses the exact same target as the MCP server.
  3. Defaults: http://localhost:8539 / root / (no password) / memory

Also probes MCP LIVENESS: it launches the configured `arangodb-memory-mcp` server
and asserts the shared-memory tools are actually exposed. Every other check here
talks to ArangoDB directly, so without this a dead or `readonly`-profiled server
reports perfectly healthy while agents have no memory at all. Skip with --no-mcp.

Usage (via the server's virtualenv, which has python-arango):
    cd ~/code/arango-solutions-mcp-server
    .venv/bin/python ~/code/arango-shared-memory/scripts/verify.py [--no-mcp]

Exit code 0 = all checks passed, 1 = a check failed, 2 = python-arango missing.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write(
        "error: python-arango is not installed.\n"
        "Run via the server's Poetry env:\n"
        "    cd ~/code/arango-solutions-mcp-server && poetry run python "
        "~/code/arango-shared-memory/scripts/verify.py\n"
    )
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"
COLLECTIONS = ["shared_patterns", "project_registry", "drift_alerts", "search_log"]
# Added by scripts/migrate.py (or a current setup_schema.py) — a miss means the
# database predates the migration round and needs `scripts/migrate.py`.
MIGRATED_COLLECTIONS = ["prd_patches", "sync_observations", "schema_migrations"]
PHASE1_VIEW = "patterns_search"
GRAPH = "memory_graph"
EDGE_COLLECTIONS = ["pattern_relates_to", "pattern_supersedes", "pattern_from_project",
                    "alert_from_project", "pattern_addresses_requirement",
                    "requirement_depends_on", "patch_from_project",
                    "observation_from_project"]
EXPECTED_INDEXES = {
    "shared_patterns": ["problem_category", "project_type", "created_at"],
    "drift_alerts": ["project_id", "status", "detected_at"],
    "search_log": ["project_id", "created_at"],
    "prd_patches": ["project_id", "review_state", "created_at"],
    "sync_observations": ["project_id", "state", "created_at"],
}

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, msg: str) -> None:
        print(f"  {GREEN}PASS{RESET}  {msg}")

    def fail(self, msg: str) -> None:
        self.failed += 1
        print(f"  {RED}FAIL{RESET}  {msg}")

    def info(self, msg: str) -> None:
        print(f"        {msg}")


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


# --- MCP liveness ---------------------------------------------------------
# Every other check here talks to ArangoDB DIRECTLY (python-arango), which means
# they all pass while the MCP server — the only path an agent actually uses — is
# dead, mis-launched, or under-profiled. That blind spot has caused three
# multi-day silent outages: an AQL reserved-word parse error that made the
# SessionStart digest inert, the removal of the `main.py` entry point that MCP
# configs launched, and MCP_PROFILE defaulting to `readonly` (which drops every
# write tool AND the whole `memory` category). All three reported ALL CHECKS
# PASSED. Because the skills are fail-open by design, total unavailability looks
# exactly like "nothing to report" — so it must be probed explicitly.
MEMORY_TOOLS = ["pattern-search", "save-pattern", "pattern-applied", "save-drift-alert"]
MCP_TIMEOUT = 45  # generous: the server opens a real ArangoDB connection at startup


def _mcp_server_entry():
    """The full `arangodb-memory-mcp` client config (command/args/env), or None."""
    for path in ["~/.cursor/mcp.json", "~/.claude.json"]:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                entry = json.load(f)["mcpServers"][SERVER_ID]
            if entry.get("command"):
                return entry, path
        except (KeyError, json.JSONDecodeError, OSError):
            continue
    return None, None


def check_mcp_liveness(c) -> None:
    """Launch the configured MCP server and assert the memory tools are exposed.

    Deliberately uses the client config verbatim — the same command, args and env
    an agent would get — so this catches launch-command drift and profile
    misconfiguration, not just "is the database up".
    """
    import subprocess

    entry, src = _mcp_server_entry()
    if not entry:
        c.info("MCP liveness: no 'arangodb-memory-mcp' client config found — skipped "
               "(register it per setup.md STEP 3 to enable this check)")
        return

    env = {**os.environ, **{k: str(v) for k, v in (entry.get("env") or {}).items()}}
    cmd = [entry["command"], *(entry.get("args") or [])]
    # initialize -> initialized -> tools/list, written in one shot; the server
    # processes them in order and we parse whatever JSON-RPC lines come back.
    stdin = "".join(json.dumps(m) + "\n" for m in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "verify.py", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ))
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              timeout=MCP_TIMEOUT, cwd=entry.get("cwd") or None, env=env)
    except subprocess.TimeoutExpired:
        c.fail(f"MCP server did not respond within {MCP_TIMEOUT}s (config: {src})")
        return
    except OSError as exc:
        c.fail(f"MCP server could not be launched ({exc}) — check 'command' in {src}")
        return

    responses = {}
    instructions = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") in (1, 2):
            responses[msg["id"]] = msg
        if msg.get("id") == 1:
            instructions = ((msg.get("result") or {}).get("instructions") or "")

    if 1 not in responses:
        tail = " | ".join((proc.stderr or "").strip().splitlines()[-2:])[:220]
        c.fail(f"MCP server failed to start or complete a handshake (config: {src})"
               + (f" — stderr: {tail}" if tail else ""))
        return

    tools = [t.get("name") for t in ((responses.get(2, {}).get("result") or {}).get("tools") or [])]
    if not tools:
        c.fail("MCP server started but returned no tools from tools/list")
        return
    c.ok(f"MCP server reachable — {len(tools)} tool(s) exposed")

    for line in instructions.splitlines():
        s = line.strip()
        if s.startswith("Active profile:") or s.startswith("Additive toolsets:"):
            c.info(s)

    missing = [t for t in MEMORY_TOOLS if t not in tools]
    if missing:
        c.fail(f"memory tools NOT exposed via MCP: {missing} — the agent cannot use shared "
               f"memory. Most likely MCP_PROFILE is 'readonly' (it excludes the whole "
               f"'memory' category); set MCP_PROFILE=developer in {src}, then restart the client")
    else:
        c.ok(f"all {len(MEMORY_TOOLS)} shared-memory tools exposed via MCP")


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    username = resolve("ARANGO_ROOT_USERNAME", "root")
    password = resolve("ARANGO_ROOT_PASSWORD", "")
    db_name = resolve("ARANGO_DEFAULT_DB_NAME", "memory")

    print(f"Shared-memory verification — {hosts}  db={db_name!r}  user={username!r}")
    print("=" * 64)
    c = Checks()

    # --- connectivity ---
    # Connect to the TARGET database directly (not _system): scoped teammate users
    # have rw on `memory` only and no _system access, so a _system-based check 401s.
    verify_ssl = resolve("ARANGO_VERIFY_SSL", "true").lower() not in ("0", "false", "no", "off", "")
    try:
        client = ArangoClient(hosts=hosts, verify_override=verify_ssl)
        db = client.db(db_name, username=username, password=password)
        db.properties()  # authenticated round-trip against the target db
    except Exception as exc:  # noqa: BLE001
        c.fail(f"could not connect to db {db_name!r} as {username!r}: {exc}")
        print("\nAborting — fix connectivity / credentials first.")
        return 1
    try:
        ver = db.version()
    except Exception:  # noqa: BLE001 — /_api/version can require _system; fine to skip
        ver = "(version check needs _system; skipped for scoped user)"
    c.ok(f"connected to ArangoDB {ver}")
    c.ok(f"database {db_name!r} accessible as {username!r}")

    # --- collections + indexes ---
    for name in COLLECTIONS:
        if db.has_collection(name):
            c.ok(f"collection {name!r} exists")
        else:
            c.fail(f"collection {name!r} missing — run scripts/setup_schema.py")
    for name in MIGRATED_COLLECTIONS:
        if db.has_collection(name):
            c.ok(f"collection {name!r} exists")
        else:
            c.fail(f"collection {name!r} missing — run scripts/migrate.py")
    for name, fields in EXPECTED_INDEXES.items():
        if not db.has_collection(name):
            continue
        present = any(
            idx.get("type") == "persistent" and list(idx.get("fields", [])) == fields
            for idx in db.collection(name).indexes()
        )
        (c.ok if present else c.fail)(f"index on {name} {fields} {'present' if present else 'missing'}")

    # --- schema validation (added by migrate.py / current setup_schema.py) ---
    if db.has_collection("shared_patterns"):
        has_schema = bool((db.collection("shared_patterns").properties() or {}).get("schema"))
        (c.ok if has_schema else c.fail)(
            "schema validation on shared_patterns "
            + ("present" if has_schema else "missing — run scripts/migrate.py"))

    # --- read/write round-trip (non-polluting: insert then delete) ---
    if db.has_collection("shared_patterns"):
        probe_key = "_verify_probe_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        coll = db.collection("shared_patterns")
        try:
            probe = {"_key": probe_key, "project_id": "_verify",
                     "problem_description": "verification probe", "worked": True}
            # A non-sparse vector index rejects inserts lacking the embedding
            # ("Expecting type Array"), so the probe must carry a dummy vector
            # of the index's dimension (embed-then-insert, same rule as save-pattern).
            for ix in coll.indexes():
                if ix.get("type") == "vector" and list(ix.get("fields", [])) == ["embedding"]:
                    dim = ix.get("params", {}).get("dimension", 1536)
                    probe["embedding"] = [1.0 / dim] * dim
                    break
            coll.insert(probe)
            got = coll.get(probe_key)
            if got and got.get("problem_description") == "verification probe":
                c.ok("write -> read round-trip succeeded")
            else:
                c.fail("round-trip read did not return the probe document")
            coll.delete(probe_key)
            if coll.get(probe_key) is None:
                c.ok("probe cleanup (delete) succeeded")
            else:
                c.fail("probe document was not deleted")
        except Exception as exc:  # noqa: BLE001
            c.fail(f"round-trip failed: {exc}")
            try:
                coll.delete(probe_key, ignore_missing=True)
            except Exception:  # noqa: BLE001
                pass

    # --- Phase 1a readiness (BM25 view + graded-scoring fields) ---
    if db.has_collection("shared_patterns"):
        view_present = any(v.get("name") == PHASE1_VIEW for v in db.views())
        (c.ok if view_present else c.fail)(
            f"ArangoSearch view {PHASE1_VIEW!r} {'present' if view_present else 'missing — run scripts/phase1_setup.py'}")
        # Scoring fields are informational: a fail only if patterns exist but lack them.
        total = next(iter(db.aql.execute("RETURN LENGTH(shared_patterns)")))
        if total:
            missing = next(iter(db.aql.execute(
                "RETURN LENGTH(FOR p IN shared_patterns "
                "FILTER p.importance == null OR p.usage_count == null OR p.last_used == null RETURN 1)")))
            if missing == 0:
                c.ok(f"graded-scoring fields present on all {total} pattern(s)")
            else:
                c.fail(f"{missing}/{total} pattern(s) missing importance/usage_count/last_used "
                       "— run scripts/phase1_setup.py")
        else:
            c.info("graded-scoring fields: no patterns yet (nothing to backfill)")
        # memory_type coverage (taxonomy backfill state).
        if total:
            untyped = next(iter(db.aql.execute(
                "RETURN LENGTH(FOR p IN shared_patterns FILTER p.memory_type == null RETURN 1)")))
            if untyped == 0:
                c.ok(f"memory_type present on all {total} memory document(s)")
            else:
                c.fail(f"{untyped}/{total} memory document(s) missing memory_type "
                       "— run scripts/migrate.py")

    # --- graph + vector layer ---
    # Informational when embeddings were never configured; a hard failure only
    # when the layer is half-installed (vector index without the graph).
    vector_idx = False
    if db.has_collection("shared_patterns"):
        vector_idx = any(ix.get("type") == "vector" and list(ix.get("fields", [])) == ["embedding"]
                         for ix in db.collection("shared_patterns").indexes())
    graph_present = db.has_graph(GRAPH)
    if graph_present:
        c.ok(f"graph {GRAPH!r} present")
        existing_defs = {e["edge_collection"] for e in db.graph(GRAPH).edge_definitions()}
        missing_defs = [e for e in EDGE_COLLECTIONS if e not in existing_defs]
        if missing_defs:
            c.fail(f"edge definitions missing from {GRAPH}: {missing_defs} — run scripts/migrate.py")
        else:
            c.ok(f"all {len(EDGE_COLLECTIONS)} edge definitions present")
        edge_counts = []
        for e in EDGE_COLLECTIONS:
            if db.has_collection(e):
                n = next(iter(db.aql.execute(f"RETURN LENGTH({e})")))
                if n:
                    edge_counts.append(f"{e}={n}")
        if edge_counts:
            c.info("edges: " + ", ".join(edge_counts))
    elif vector_idx:
        c.fail(f"vector index present but graph {GRAPH!r} missing — run scripts/phase2_setup.py")
    else:
        c.info(f"graph {GRAPH!r} not installed (no embeddings yet) — phase1b/phase2 when ready")
    c.info(f"vector index on shared_patterns.embedding: {'present' if vector_idx else 'absent (keyword-only)'}")
    if db.has_collection("drift_alerts"):
        ttl = any(ix.get("type") == "ttl" and list(ix.get("fields", [])) == ["closed_at"]
                  for ix in db.collection("drift_alerts").indexes())
        c.info(f"TTL index on drift_alerts.closed_at: "
               f"{'present' if ttl else 'absent — phase3_lifecycle.py adds it'}")

    # --- adoption snapshot ---
    print("\nAdoption snapshot")
    print("-" * 64)

    def count(coll_name: str, filt: str = "") -> int:
        q = f"RETURN LENGTH(FOR d IN {coll_name} {filt} RETURN 1)"
        return next(iter(db.aql.execute(q)))

    if db.has_collection("shared_patterns"):
        print(f"  shared_patterns:   {count('shared_patterns')} pattern(s)")
    if db.has_collection("drift_alerts"):
        opened = count("drift_alerts", 'FILTER d.status == "open"')
        closed = count("drift_alerts", 'FILTER d.status == "closed"')
        print(f"  drift_alerts:      {opened} open / {closed} closed")
    if db.has_collection("prd_patches"):
        by_state = list(db.aql.execute(
            "FOR p IN prd_patches COLLECT s = p.review_state WITH COUNT INTO n RETURN {s, n}"))
        if by_state:
            print("  prd_patches:       " + ", ".join(f"{r['s']}={r['n']}" for r in by_state))
            pending = next((r["n"] for r in by_state if r["s"] == "proposed"), 0)
            if pending:
                print(f"      {YELLOW}{pending} proposed patch(es) awaiting review — "
                      f"run /prd-sync Phase 6 in the affected project(s){RESET}")
        else:
            print("  prd_patches:       0")
    if db.has_collection("sync_observations"):
        by_state = list(db.aql.execute(
            "FOR o IN sync_observations COLLECT s = o.state WITH COUNT INTO n RETURN {s, n}"))
        print("  sync_observations: "
              + (", ".join(f"{r['s']}={r['n']}" for r in by_state) if by_state else "0"))
    if db.has_collection("project_registry"):
        rows = list(db.aql.execute(
            "FOR p IN project_registry SORT p.project_id RETURN p"))
        print(f"  project_registry:  {len(rows)} project(s) registered")
        for p in rows:
            print(f"      - {p.get('project_id','?'):32} "
                  f"gaps={p.get('open_gaps','-')}  "
                  f"patterns={p.get('patterns_contributed','-')}  "
                  f"last_sync={p.get('last_sync','never')}")
        if not rows:
            print(f"      {YELLOW}(none yet — run /prd-sync or /pattern-save in a project){RESET}")

    # --- read-path scorecard (is shared memory actually being READ + REUSED?) ---
    print("\nRead-path scorecard  (the value metric — not just writes)")
    print("-" * 64)
    total_applies, searches = 0, 0
    if db.has_collection("shared_patterns"):
        applied = count("shared_patterns", "FILTER d.usage_count > 0")
        total_applies = next(iter(db.aql.execute(
            "RETURN SUM(FOR p IN shared_patterns RETURN p.usage_count == null ? 0 : p.usage_count)")))
        surfaced = count("shared_patterns", "FILTER d.surfaced_count > 0")
        npat = count("shared_patterns")
        print(f"  patterns applied (usage_count>0):   {applied}/{npat}   (total applies: {total_applies})")
        print(f"  patterns ever surfaced by search:   {surfaced}/{npat}")
        funnel = f"{100*applied/surfaced:.0f}%" if surfaced else "n/a"
        print(f"  surfaced->applied conversion:       {applied}/{surfaced}  ({funnel})   "
              f"{YELLOW}<- the leak to watch{RESET}")
        pending = count("shared_patterns", "FILTER d.embedding_pending == true")
        if pending:
            print(f"  {YELLOW}embeddings deferred (pending backfill): {pending} "
                  f"— run pattern-index or phase1b_setup.py{RESET}")
    # Read-path telemetry (independent of whether an eval history exists).
    # SessionStart recalls are real memory reads, but they are separated from
    # interactive pattern-search calls so they do not dilute apply/search.
    if db.has_collection("search_log"):
        searches = count("search_log", 'FILTER d.mode != "session_recall"')
        recalls = count("search_log", 'FILTER d.mode == "session_recall"')
        hits = count("search_log", 'FILTER d.mode != "session_recall" AND d.hit == true')
        rate = f"{100*hits/searches:.0f}%" if searches else "n/a"
        print(f"  interactive searches logged:        {searches}   "
              f"(hit rate ≥0.5 relevance: {rate})")
        print(f"  automatic SessionStart recalls:      {recalls}")
        apprate = f"{total_applies/searches:.2f}" if searches else "n/a"
        print(f"  apply events per interactive search: {apprate}")
        by_proj = list(db.aql.execute(
            'FOR s IN search_log FILTER s.project_id != null COLLECT p = s.project_id '
            'AGGREGATE searches = SUM(s.mode == "session_recall" ? 0 : 1), '
            'recalls = SUM(s.mode == "session_recall" ? 1 : 0) '
            'SORT searches + recalls DESC RETURN {p, searches, recalls}'))
        if by_proj:
            print("  reads by project: "
                  + ", ".join(f"{r['p']}={r['searches']} search/{r['recalls']} recall"
                              for r in by_proj))
        if db.has_collection("project_registry"):
            zero = list(db.aql.execute(
                "LET readers = UNIQUE(FOR s IN search_log FILTER s.project_id != null "
                "RETURN s.project_id) FOR p IN project_registry "
                "FILTER p.project_id NOT IN readers SORT p.project_id RETURN p.project_id"))
            print(f"  registered projects with no read:    {len(zero)}"
                  + (f"   ({', '.join(zero)})" if zero else ""))
        if not searches:
            print(f"      {YELLOW}(no interactive searches logged yet — reads are how memory pays off; "
                  f"run /pattern-search before solving){RESET}")
    else:
        print(f"      {YELLOW}(search_log absent — instrumentation not yet active; reload the "
              f"MCP server after updating pattern-search){RESET}")

    # Retrieval-quality eval history (independent of search_log).
    if db.has_collection("eval_runs"):
        last = next(iter(db.aql.execute(
            "FOR r IN eval_runs SORT r.run_at DESC LIMIT 1 RETURN r")), None)
        if last:
            summary = "  ".join(f"{m}: MRR={v['mrr']:.2f} R@5={v['recall@5']:.2f}"
                                for m, v in sorted(last["modes"].items()))
            print(f"  retrieval eval ({last['run_at'][:10]}, n={last['n_queries']}): {summary}")
        else:
            print(f"      {YELLOW}(no eval runs yet — run scripts/eval_retrieval.py){RESET}")

    # --- MCP liveness (the agent-facing path; see check_mcp_liveness) ---
    print("\nMCP liveness  (the path agents actually use — DB checks above cannot see it)")
    print("-" * 64)
    if "--no-mcp" in sys.argv:
        c.info("skipped (--no-mcp)")
    else:
        check_mcp_liveness(c)

    # --- summary ---
    print("\n" + "=" * 64)
    if c.failed == 0:
        print(f"{GREEN}ALL CHECKS PASSED{RESET} — shared memory is healthy.")
        return 0
    print(f"{RED}{c.failed} CHECK(S) FAILED{RESET} — see above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
