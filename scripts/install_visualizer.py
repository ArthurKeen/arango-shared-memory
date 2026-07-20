#!/usr/bin/env python3
"""Install ArangoDB Graph Visualizer assets for the shared-memory `memory_graph`.

Idempotent. Two parts:

  1. GRAPH COMPLETENESS (de-orphan) — ensure every pattern and every drift alert
     links to its project node, so `project_registry` and `drift_alerts` are not
     orphan islands in the Visualizer:
       - `pattern_from_project`  (shared_patterns -> project_registry)   [existing]
       - `alert_from_project`    (drift_alerts   -> project_registry)   [added here]
     Missing project_registry docs are auto-created (marked `autocreated: true`).

  2. VISUALIZER ASSETS — a theme (colors/icons + status/usage rules) in
     `_graphThemeStore`, saved AQL queries in `_editor_saved_queries`, starter
     graph loads in `_queries` (the Visualizer "Queries" panel, linked via
     `_viewpointQueries`), and right-click canvas actions in `_canvasActions`
     (linked via `_viewpointActions`).

Managed / cloud deployment (prod.demo cluster): ALL Visualizer collections live
in the TARGET DB (`memory`), not `_system` — the scoped MCP user has no `_system`
access. That is the default here.

Connection is resolved like verify.py (first wins):
  1. env: ARANGO_HOSTS / ARANGO_ROOT_USERNAME / ARANGO_ROOT_PASSWORD /
          ARANGO_DEFAULT_DB_NAME / ARANGO_VERIFY_SSL
  2. the `arangodb-memory-mcp` entry in ~/.cursor/mcp.json or ~/.claude.json
  3. defaults: http://localhost:8539 / root / (no password) / memory

Usage (via the server's Poetry env, which has python-arango):
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/install_visualizer.py

Options:
    --graph NAME        graph to target (default: memory_graph)
    --no-backfill       install visualizer assets only; skip graph completeness
    --default           install the theme as the auto-applied default (otherwise
                        it is selectable from the Legend; see notes below)
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write(
        "error: python-arango is not installed.\n"
        "Run via the server's Poetry env:\n"
        "    cd ~/code/arango-solutions-mcp-server && poetry run python "
        "~/code/arango-shared-memory/scripts/install_visualizer.py\n"
    )
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"
THEME_NAME = "shared_memory"


# --------------------------------------------------------------------------- #
# connection (mirrors verify.py)
# --------------------------------------------------------------------------- #
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# --------------------------------------------------------------------------- #
# small idempotent helpers
# --------------------------------------------------------------------------- #
def ensure_collection(db, name: str, edge: bool = False) -> None:
    if not db.has_collection(name):
        db.create_collection(name, edge=edge, system=name.startswith("_"))


def upsert_by_key(col, key: str, doc: dict) -> str:
    doc = {**doc, "_key": key}
    if col.has(key):
        existing = col.get(key)
        doc.setdefault("createdAt", existing.get("createdAt"))
        col.replace(doc, check_rev=False)
    else:
        col.insert(doc)
    return f"{col.name}/{key}"


def ensure_default_viewpoint(db, graph_name: str) -> str:
    ensure_collection(db, "_viewpoints")
    vp = db.collection("_viewpoints")
    for q in ({"graphId": graph_name, "name": "Default"}, {"graphId": graph_name}):
        found = list(vp.find(q))
        if found:
            return found[0]["_id"]
    now = now_iso()
    return vp.insert({"graphId": graph_name, "name": "Default",
                      "description": f"Default viewpoint for {graph_name}",
                      "createdAt": now, "updatedAt": now})["_id"]


def ensure_link(db, coll: str, frm: str, to: str) -> None:
    ensure_collection(db, coll, edge=True)
    ec = db.collection(coll)
    if not list(ec.find({"_from": frm, "_to": to})):
        ec.insert({"_from": frm, "_to": to, "createdAt": now_iso()})


# --------------------------------------------------------------------------- #
# Part 1 — graph completeness (de-orphan)
# --------------------------------------------------------------------------- #
def complete_graph(db, graph_name: str) -> None:
    if not db.has_graph(graph_name):
        print(f"  graph {graph_name!r} not found — skipping completeness")
        return
    g = db.graph(graph_name)
    edefs = {ed["edge_collection"] for ed in g.edge_definitions()}

    # alert_from_project: drift_alerts -> project_registry
    if "alert_from_project" not in edefs:
        g.create_edge_definition(
            edge_collection="alert_from_project",
            from_vertex_collections=["drift_alerts"],
            to_vertex_collections=["project_registry"])
        print("  + added edge definition alert_from_project (drift_alerts -> project_registry)")
    else:
        print("  = edge definition alert_from_project already present")

    # every project_id referenced by a pattern or an alert must have a node
    db.aql.execute("""
      FOR pid IN UNIQUE(APPEND(
            (FOR p IN shared_patterns RETURN p.project_id),
            (FOR d IN drift_alerts RETURN d.project_id)))
        FILTER pid != null
        UPSERT { _key: pid }
        INSERT { _key: pid, project_id: pid, project_name: pid, project_type: "other",
                 open_gaps: 0, patterns_contributed: 0, last_sync: null, autocreated: true }
        UPDATE { } IN project_registry
    """)

    # one provenance edge per pattern (deterministic key == what save-pattern uses)
    n_fp = list(db.aql.execute("""
      FOR p IN shared_patterns
        LET ek = CONCAT(p._key, "__", p.project_id)
        UPSERT { _key: ek }
        INSERT { _key: ek, _from: p._id, _to: CONCAT("project_registry/", p.project_id),
                 relation: "from_project" }
        UPDATE { _from: p._id, _to: CONCAT("project_registry/", p.project_id) }
          IN pattern_from_project
        RETURN 1
    """))
    # one provenance edge per drift alert
    n_ap = list(db.aql.execute("""
      FOR d IN drift_alerts
        LET ek = CONCAT(d._key, "__", d.project_id)
        UPSERT { _key: ek }
        INSERT { _key: ek, _from: d._id, _to: CONCAT("project_registry/", d.project_id),
                 relation: "alert_from_project" }
        UPDATE { _from: d._id, _to: CONCAT("project_registry/", d.project_id) }
          IN alert_from_project
        RETURN 1
    """))
    orphans = list(db.aql.execute("""
      LET connected = UNIQUE(APPEND(
        (FOR e IN pattern_from_project RETURN e._to),
        (FOR e IN alert_from_project RETURN e._to)))
      FOR pr IN project_registry FILTER pr._id NOT IN connected RETURN pr._key
    """))
    print(f"  = {len(n_fp)} pattern_from_project + {len(n_ap)} alert_from_project edges ensured")
    print(f"  = project nodes still orphan (no patterns AND no alerts): {orphans or 'none'}")


# --------------------------------------------------------------------------- #
# Part 2a — theme
# --------------------------------------------------------------------------- #
def _rule(attr, atype, op, value, color, icon="mdi:table"):
    """One nested attribute-based theme rule (VERIFIED schema — color only)."""
    return {
        "id": str(uuid.uuid4()),
        "attributePath": attr, "attributeType": atype, "conditionType": "singleValue",
        "condition": {
            "op": op, "right": {"type": "literal", "value": value},
            "config": {"background": {"color": color, "iconName": icon},
                       "labelAttribute": "", "hoverInfoAttributes": [], "rules": []},
            "enabledFields": {"color": True, "icon": False,
                              "labelAttribute": False, "hoverInfoAttributes": False},
        },
    }


def _edge(color, thickness=1.2):
    return {
        "lineStyle": {"color": color, "thickness": thickness},
        "arrowStyle": {"sourceArrowShape": "none", "targetArrowShape": "triangle"},
        "labelStyle": {"color": "#1d2531"}, "hoverInfoAttributes": [], "rules": [],
    }


def build_theme(graph_id: str, is_default: bool) -> dict:
    node_config = {
        "shared_patterns": {
            "background": {"color": "#3182ce", "iconName": "fa6-solid:lightbulb"},
            "labelAttribute": "problem_category",
            "hoverInfoAttributes": ["problem_description", "solution_summary",
                                    "project_id", "importance", "usage_count", "worked"],
            "rules": [
                # first match wins: reused (green) beats high-importance (gold)
                _rule("usage_count", "number", ">=", 1, "#38a169"),
                _rule("importance", "number", ">=", 8, "#d69e2e"),
            ],
        },
        "project_registry": {
            "background": {"color": "#805ad5", "iconName": "fa6-solid:diagram-project"},
            "labelAttribute": "project_id",
            "hoverInfoAttributes": ["project_name", "patterns_contributed",
                                    "open_gaps", "project_type", "last_sync"],
            "rules": [_rule("open_gaps", "number", ">=", 1, "#dd6b20")],
        },
        "drift_alerts": {
            "background": {"color": "#718096", "iconName": "fa6-solid:triangle-exclamation"},
            "labelAttribute": "req_id",
            "hoverInfoAttributes": ["requirement", "classification",
                                    "status", "project_id", "gap_description"],
            "rules": [
                _rule("status", "string", "==", "open", "#e53e3e"),
                _rule("status", "string", "==", "closed", "#48bb78"),
            ],
        },
    }
    edge_config = {
        "pattern_relates_to": _edge("#3182ce"),            # similarity (blue)
        "pattern_from_project": _edge("#718096", 1.0),     # provenance (grey)
        "alert_from_project": _edge("#38a169", 1.0),       # provenance (green)
        "pattern_addresses_requirement": _edge("#dd6b20"),  # addresses (orange)
        "pattern_supersedes": _edge("#e53e3e"),            # supersedes (red)
        "requirement_depends_on": _edge("#805ad5"),        # dependency (purple)
    }
    return {
        "graphId": graph_id, "name": THEME_NAME,
        "description": "Shared-memory graph: patterns (blue; green=reused, gold=high-importance), "
                       "projects (purple), drift alerts (red=open, green=closed).",
        "isDefault": is_default,
        "nodeConfigMap": node_config, "edgeConfigMap": edge_config,
    }


def install_theme(db, graph_id: str, is_default: bool) -> None:
    ensure_collection(db, "_graphThemeStore")
    col = db.collection("_graphThemeStore")

    # keep exactly one isDefault:true for this graph. Ensure a plain built-in
    # "Default" exists so default styling is never lost (skill guidance).
    if not list(col.find({"graphId": graph_id, "name": "Default"})):
        col.insert({"graphId": graph_id, "name": "Default", "isDefault": not is_default,
                    "description": "Built-in default styling.",
                    "nodeConfigMap": {}, "edgeConfigMap": {},
                    "createdAt": now_iso(), "updatedAt": now_iso()})

    theme = build_theme(graph_id, is_default)
    theme["updatedAt"] = now_iso()
    existing = list(col.find({"graphId": graph_id, "name": THEME_NAME}))
    if existing:
        theme["_key"] = existing[0]["_key"]
        theme["createdAt"] = existing[0].get("createdAt", theme["updatedAt"])
        col.replace(theme, check_rev=False)
    else:
        theme["createdAt"] = theme["updatedAt"]
        col.insert(theme)

    if is_default:  # ensure this is the ONLY default for the graph
        for t in col.find({"graphId": graph_id}):
            want = t.get("name") == THEME_NAME
            if bool(t.get("isDefault")) != want:
                col.update({"_key": t["_key"], "isDefault": want}, check_rev=False)
    print(f"  = theme {THEME_NAME!r} installed (isDefault={is_default})")


# --------------------------------------------------------------------------- #
# Part 2b — saved queries (global AQL editor)
# --------------------------------------------------------------------------- #
SAVED_QUERIES = [
    {"_key": "sm_top_reused_patterns", "title": "Shared memory: top reused patterns",
     "aql": "FOR p IN shared_patterns FILTER p.usage_count > 0\n"
            "  SORT p.usage_count DESC, p.importance DESC\n"
            "  RETURN {pattern: p.problem_category, project: p.project_id,\n"
            "          applies: p.usage_count, surfaced: p.surfaced_count, importance: p.importance}"},
    {"_key": "sm_surfaced_not_applied", "title": "Shared memory: surfaced-but-never-applied (the funnel gap)",
     "aql": "FOR p IN shared_patterns FILTER (p.surfaced_count > 0) AND (p.usage_count == null OR p.usage_count == 0)\n"
            "  SORT p.surfaced_count DESC\n"
            "  RETURN {pattern: p.problem_category, project: p.project_id, surfaced: p.surfaced_count}"},
    {"_key": "sm_open_drift_by_project", "title": "Shared memory: open drift alerts by project",
     "aql": "FOR d IN drift_alerts FILTER d.status == 'open'\n"
            "  COLLECT project = d.project_id WITH COUNT INTO open_gaps\n"
            "  SORT open_gaps DESC RETURN {project, open_gaps}"},
    {"_key": "sm_cross_project_related", "title": "Shared memory: cross-project related patterns",
     "aql": "FOR e IN pattern_relates_to\n"
            "  LET a = DOCUMENT(e._from) LET b = DOCUMENT(e._to)\n"
            "  FILTER a.project_id != b.project_id\n"
            "  RETURN DISTINCT {a: a.problem_category, a_proj: a.project_id,\n"
            "                   b: b.problem_category, b_proj: b.project_id}"},
    {"_key": "sm_project_contributions", "title": "Shared memory: contributions per project",
     "aql": "FOR pr IN project_registry\n"
            "  LET patterns = LENGTH(FOR e IN pattern_from_project FILTER e._to == pr._id RETURN 1)\n"
            "  LET alerts   = LENGTH(FOR e IN alert_from_project  FILTER e._to == pr._id RETURN 1)\n"
            "  SORT patterns DESC RETURN {project: pr.project_id, patterns, alerts}"},
]


def install_saved_queries(db) -> None:
    ensure_collection(db, "_editor_saved_queries")
    col = db.collection("_editor_saved_queries")
    for q in SAVED_QUERIES:
        upsert_by_key(col, q["_key"], {
            "title": q["title"], "name": q["title"], "description": q["title"],
            # editor reads content + value (NOT queryText)
            "content": q["aql"], "value": q["aql"], "bindVariables": {},
            "databaseName": db.name, "updatedAt": now_iso(),
        })
    print(f"  = {len(SAVED_QUERIES)} saved queries installed (_editor_saved_queries)")


# --------------------------------------------------------------------------- #
# Part 2c — Visualizer "Queries" panel + canvas actions
# --------------------------------------------------------------------------- #
def panel_queries(graph_id: str) -> list[dict]:
    return [
        {"name": "Load: patterns + projects (provenance)",
         "queryText": "FOR e IN pattern_from_project LIMIT 200 RETURN e"},
        {"name": "Load: pattern similarity network",
         "queryText": "FOR e IN pattern_relates_to LIMIT 300 RETURN e"},
        {"name": "Load: drift alerts by project",
         "queryText": "FOR e IN alert_from_project LIMIT 300 RETURN e"},
        {"name": "Load: patterns that address requirements",
         "queryText": "FOR e IN pattern_addresses_requirement LIMIT 200 RETURN e"},
        {"name": "Load: full graph (sampled)",
         "queryText": "FOR c IN [pattern_from_project, alert_from_project, pattern_relates_to,\n"
                      "          pattern_addresses_requirement, requirement_depends_on, pattern_supersedes]\n"
                      "  FOR e IN c LIMIT 80 RETURN e"},
    ]


def canvas_actions(graph_id: str) -> list[dict]:
    G = graph_id
    return [
        {"name": "Expand: related patterns (1-2 hops)",
         "queryText": f'FOR node IN @nodes\n'
                      f'  FOR v, e IN 1..2 ANY node pattern_relates_to\n'
                      f'  LIMIT 50 RETURN e'},
        {"name": "Expand: provenance (project + requirements)",
         "queryText": f'FOR node IN @nodes\n'
                      f'  FOR v, e, p IN 1..1 ANY node pattern_from_project, alert_from_project,'
                      f' pattern_addresses_requirement\n'
                      f'  LIMIT 50 RETURN p'},
        {"name": "Expand: everything 1 hop",
         "queryText": f'FOR node IN @nodes\n'
                      f'  FOR v, e, p IN 1..1 ANY node GRAPH "{G}"\n'
                      f'  LIMIT 60 RETURN p'},
        {"name": "Expand: this project's patterns + alerts",
         "queryText": f'FOR node IN @nodes\n'
                      f'  FILTER IS_SAME_COLLECTION("project_registry", node)\n'
                      f'  FOR v, e, p IN 1..1 INBOUND node pattern_from_project, alert_from_project\n'
                      f'  LIMIT 100 RETURN p'},
    ]


def install_panel_and_actions(db, graph_id: str) -> None:
    vp_id = ensure_default_viewpoint(db, graph_id)

    ensure_collection(db, "_queries")
    qcol = db.collection("_queries")
    for q in panel_queries(graph_id):
        key = _slug(f"{graph_id}_{q['name']}")
        qid = upsert_by_key(qcol, key, {
            "name": q["name"], "title": q["name"], "description": q["name"],
            "graphId": graph_id, "queryText": q["queryText"], "bindVariables": {},
            "updatedAt": now_iso(),
        })
        ensure_link(db, "_viewpointQueries", vp_id, qid)
    print(f"  = {len(panel_queries(graph_id))} Visualizer panel queries installed (_queries)")

    ensure_collection(db, "_canvasActions")
    acol = db.collection("_canvasActions")
    acts = canvas_actions(graph_id)
    for a in acts:
        key = _slug(f"{graph_id}_{a['name']}")
        aid = upsert_by_key(acol, key, {
            "name": a["name"], "title": a["name"], "description": a["name"],
            "graphId": graph_id, "queryText": a["queryText"],
            "bindVariables": {"nodes": []}, "updatedAt": now_iso(),
        })
        ensure_link(db, "_viewpointActions", vp_id, aid)
    print(f"  = {len(acts)} canvas actions installed (_canvasActions)")


# --------------------------------------------------------------------------- #
def main() -> int:
    graph = "memory_graph"
    if "--graph" in sys.argv:
        graph = sys.argv[sys.argv.index("--graph") + 1]
    do_backfill = "--no-backfill" not in sys.argv
    is_default = "--default" in sys.argv

    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    username = resolve("ARANGO_ROOT_USERNAME", "root")
    password = resolve("ARANGO_ROOT_PASSWORD", "")
    db_name = resolve("ARANGO_DEFAULT_DB_NAME", "memory")
    verify_ssl = resolve("ARANGO_VERIFY_SSL", "true").lower() not in ("0", "false", "no", "off", "")

    print(f"Visualizer install — {hosts}  db={db_name!r}  graph={graph!r}  user={username!r}")
    print("=" * 68)
    try:
        db = ArangoClient(hosts=hosts, request_timeout=120,
                          verify_override=verify_ssl).db(db_name, username=username, password=password)
        db.properties()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: cannot connect to {db_name!r} as {username!r}: {exc}\n")
        return 1

    if do_backfill:
        print("Part 1 — graph completeness (de-orphan):")
        complete_graph(db, graph)
    else:
        print("Part 1 — skipped (--no-backfill)")

    print("Part 2 — Visualizer assets:")
    install_theme(db, graph, is_default)
    install_saved_queries(db)
    install_panel_and_actions(db, graph)

    print("=" * 68)
    print("Done. Reload the Graph Visualizer, then:")
    print(f"  • Legend → select the {THEME_NAME!r} theme"
          + ("" if is_default else "  (or re-run with --default to auto-apply it)"))
    print("  • Queries panel → starter graph loads")
    print("  • right-click a node → Canvas Actions → expand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
