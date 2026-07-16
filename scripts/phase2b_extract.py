#!/usr/bin/env python3
"""Phase 2b — LLM-extracted graph edges (gpt-4o).

Adds two edge types the structural/similarity pass can't infer, using the
LLM_EXTRACTION_MODEL (default gpt-4o) over the existing patterns + drift_alerts:

  - pattern_addresses_requirement  shared_patterns -> drift_alerts
        A solution pattern that resolves / would help resolve a requirement gap.
  - requirement_depends_on          drift_alerts   -> drift_alerts
        One requirement depends on another (must be satisfied first).

Bounded LLM usage: one chat call per drift_alert for addressing (patterns listed
inline), plus one call total for dependencies. JSON-mode responses. Idempotent
(deterministic edge _keys, overwrite=True).

Prereq: phase2_setup.py has run (graph exists). Requires OPENAI_API_KEY.

Usage:
    cd ~/code/arango-solutions-mcp-server
    poetry run python ~/code/arango-shared-memory/scripts/phase2b_extract.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing; run via the server's Poetry env.\n")
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"
GRAPH = "memory_graph"
ADDR, DEP = "pattern_addresses_requirement", "requirement_depends_on"
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
    return re.sub(r"[^A-Za-z0-9_-]", "-", f"{a}__{b}")[:250]


API_KEY = resolve("OPENAI_API_KEY")
LLM = resolve("LLM_EXTRACTION_MODEL", "gpt-4o")


def llm_json(system, user):
    """Call the chat model in JSON mode; return the parsed object ({} on failure)."""
    body = json.dumps({
        "model": LLM, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    if not API_KEY:
        sys.stderr.write("error: OPENAI_API_KEY not found (env or mcp.json).\n")
        return 1
    db = ArangoClient(hosts=[h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",")]).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=resolve("ARANGO_ROOT_USERNAME", "root"),
        password=resolve("ARANGO_ROOT_PASSWORD", ""))
    print(f"Phase 2b — LLM edges via {LLM}{'  [DRY RUN]' if DRY_RUN else ''}")

    # Ensure edge definitions exist in the graph.
    g = db.graph(GRAPH)
    existing = {e["edge_collection"] for e in g.edge_definitions()}
    for name, frm, to in [(ADDR, "shared_patterns", "drift_alerts"),
                          (DEP, "drift_alerts", "drift_alerts")]:
        if name not in existing and not DRY_RUN:
            g.create_edge_definition(edge_collection=name,
                                     from_vertex_collections=[frm],
                                     to_vertex_collections=[to])
            print(f"  + edge definition {name!r}")

    patterns = list(db.aql.execute(
        'FOR p IN shared_patterns RETURN {k: p._key, cat: p.problem_category, '
        '"desc": p.problem_description, sol: p.solution_summary}'))
    alerts = list(db.aql.execute(
        'FOR d IN drift_alerts RETURN {k: d._key, req: d.requirement, '
        'gap: d.gap_description, status: d.status}'))
    print(f"  {len(patterns)} patterns, {len(alerts)} drift alerts")
    if not patterns or not alerts:
        print("  nothing to link (need both patterns and drift alerts).");
        if not DRY_RUN and not alerts:
            print("\nDone."); return 0

    plist = "\n".join(f"[{i}] ({p['cat']}) {p['desc']} :: {p['sol'][:200]}"
                      for i, p in enumerate(patterns))

    # 1. pattern_addresses_requirement — one LLM call per alert.
    addr_edges = 0
    sys_a = ("You link reusable solution PATTERNS to a REQUIREMENT/gap. Return JSON "
             '{"addresses":[indices]} listing only patterns that directly resolve or '
             "materially help resolve the requirement. Be conservative; [] if none.")
    for a in alerts:
        req = a["req"] or a["gap"] or ""
        if not req:
            continue
        if DRY_RUN:
            print(f"    would ask {LLM}: which patterns address {a['k']!r}")
            continue
        out = llm_json(sys_a, f"REQUIREMENT: {req}\n\nPATTERNS:\n{plist}")
        for i in out.get("addresses", []):
            if isinstance(i, int) and 0 <= i < len(patterns):
                db.collection(ADDR).insert({
                    "_key": ekey(patterns[i]["k"], a["k"]),
                    "_from": f"shared_patterns/{patterns[i]['k']}",
                    "_to": f"drift_alerts/{a['k']}",
                    "extracted_by": LLM}, overwrite=True)
                addr_edges += 1
    print(f"  {ADDR}: {addr_edges} edge(s)")

    # 2. requirement_depends_on — one LLM call over all requirements.
    dep_edges = 0
    if not DRY_RUN and len(alerts) > 1:
        rlist = "\n".join(f"[{i}] {a['req'] or a['gap']}" for i, a in enumerate(alerts))
        sys_d = ('Given numbered REQUIREMENTS, return JSON {"deps":[[i,j]]} meaning '
                 "requirement i depends on requirement j (j must be satisfied first). "
                 "Only clear technical dependencies; [] if none.")
        out = llm_json(sys_d, f"REQUIREMENTS:\n{rlist}")
        for pair in out.get("deps", []):
            if (isinstance(pair, list) and len(pair) == 2
                    and all(isinstance(x, int) and 0 <= x < len(alerts) for x in pair)
                    and pair[0] != pair[1]):
                i, j = pair
                db.collection(DEP).insert({
                    "_key": ekey(alerts[i]["k"], alerts[j]["k"]),
                    "_from": f"drift_alerts/{alerts[i]['k']}",
                    "_to": f"drift_alerts/{alerts[j]['k']}",
                    "extracted_by": LLM}, overwrite=True)
                dep_edges += 1
    print(f"  {DEP}: {dep_edges} edge(s)")

    print("\nDry run complete — no changes made." if DRY_RUN else "\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
