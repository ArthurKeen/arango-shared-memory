#!/usr/bin/env python3
"""Retrieval-quality evaluation harness for the shared memory.

Answers, with numbers, the question the system previously could not:
*does hybrid (and the graph layer) actually beat keyword search here?*

Scores /pattern-search's three ranking modes — bm25, hybrid (vector+BM25 RRF),
hybrid+graph (1-hop relates_to expansion) — against a golden set of
query -> expected-pattern pairs:

  eval/golden_queries.json   (source of truth, reviewed in git)
        --sync-->  eval_queries collection (shared, so teammates see/extend it)
        --run -->  eval_runs collection    (append-only history: trend quality
                                            across corpus growth + ranking changes)

Metrics: recall@1/3/5 and MRR over the top-8, "any-of" semantics when a query
has several acceptable expected keys. Per-category breakdown (paraphrase =
semantic recall, keyword = lexical/error-code lookup, concept = vague
cross-project) shows *where* each mode wins.

IMPORTANT: runs the SAME AQL as the server's pattern-search tool but WITHOUT
side effects — no search_log rows, no surfaced_count bumps. Evaluation must
never distort the adoption metrics it sits beside. The AQL constants below are
copied from arango-solutions-mcp-server/mcp_tools/pattern_memory_tools.py —
if ranking changes there, update here (the eval exists to measure exactly that
ranking, so drift between the two defeats its purpose).

Connection + OPENAI_API_KEY resolve env -> arangodb-memory-mcp MCP config ->
defaults, exactly like verify.py. Hybrid modes are skipped (with a warning)
when no API key or vector index is available.

Usage (via the MCP server's venv, which has python-arango):
    cd ~/code/arango-solutions-mcp-server
    .venv/bin/python ~/code/arango-shared-memory/scripts/eval_retrieval.py
Options:
    --no-sync     score against eval_queries as-is (skip golden-file upsert)
    --dry-run     print what would be synced/run; no writes, no API calls
Exit: 0 ok · 1 failure/misconfiguration · 2 python-arango missing.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing — run via the MCP server's venv:\n"
                     "  cd ~/code/arango-solutions-mcp-server && .venv/bin/python "
                     "~/code/arango-shared-memory/scripts/eval_retrieval.py\n")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "..", "eval", "golden_queries.json")
SERVER_ID = "arangodb-memory-mcp"
TOP_K = 8
DRY_RUN = "--dry-run" in sys.argv
NO_SYNC = "--no-sync" in sys.argv

# ---------------------------------------------------------------- connection
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


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- embeddings (stdlib)
def embed(texts: list[str], model: str, api_key: str) -> list[list[float]]:
    """One batched call to the OpenAI embeddings endpoint (stdlib urllib)."""
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"input": texts, "model": model}).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120,
                                context=ssl.create_default_context()) as r:
        data = json.load(r)["data"]
    return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


# ------------------- ranking AQL — COPIED from the server's pattern-search.
# Keep byte-identical to mcp_tools/pattern_memory_tools.py (see module docstring).
_BM25_AQL = """
LET cand = (FOR p IN @@view
  SEARCH ANALYZER(
    p.problem_description IN TOKENS(@q,"text_en")
    OR p.solution_summary IN TOKENS(@q,"text_en")
    OR p.tags            IN TOKENS(@q,"text_en"), "text_en")
  LET rel = BM25(p) SORT rel DESC LIMIT 25 RETURN { p, rel })
LET maxRel = MAX(cand[*].rel)
FOR c IN cand
  FILTER c.p.superseded != true
  LET rel = c.rel / (maxRel > 0 ? maxRel : 1)
  LET imp = (c.p.importance == null ? 5 : c.p.importance) / 10.0
  LET rec = POW(0.995, DATE_DIFF(c.p.last_used == null ? c.p.created_at : c.p.last_used, DATE_NOW(), "d"))
  LET use = LOG(1 + (c.p.usage_count == null ? 0 : c.p.usage_count)) / LOG(11)
  LET aw  = c.p.applied_worked == null ? 0 : c.p.applied_worked
  LET af  = c.p.applied_failed == null ? 0 : c.p.applied_failed
  LET succ = (aw + af) == 0 ? 1 : aw / (aw + af)
  LET score = rel * (1 + 0.15*imp + 0.10*rec + 0.05*use) * (0.6 + 0.4*succ)
  SORT score DESC LIMIT @lim
  RETURN c.p._key
"""

_HYBRID_AQL = """
LET vec = (FOR p IN @@coll
             SORT APPROX_NEAR_COSINE(p.embedding, @qvec) DESC LIMIT 25 RETURN p._key)
LET bm  = (FOR p IN @@view
             SEARCH ANALYZER(
               p.problem_description IN TOKENS(@q,"text_en")
               OR p.solution_summary IN TOKENS(@q,"text_en")
               OR p.tags            IN TOKENS(@q,"text_en"), "text_en")
             SORT BM25(p) DESC LIMIT 25 RETURN p._key)
LET fused = (FOR k IN UNIQUE(APPEND(vec, bm))
  LET vr = POSITION(vec, k, true)
  LET br = POSITION(bm,  k, true)
  RETURN { k, rrf: (vr == -1 ? 0 : 1.0/(10+vr+1)) + (br == -1 ? 0 : 1.0/(10+br+1)) })
LET maxRrf = MAX(fused[*].rrf)
FOR f IN fused
  LET p = DOCUMENT(@@coll, f.k)
  FILTER p.superseded != true
  LET rel = f.rrf / (maxRrf > 0 ? maxRrf : 1)
  LET imp = (p.importance == null ? 5 : p.importance) / 10.0
  LET rec = POW(0.995, DATE_DIFF(p.last_used == null ? p.created_at : p.last_used, DATE_NOW(), "d"))
  LET use = LOG(1 + (p.usage_count == null ? 0 : p.usage_count)) / LOG(11)
  LET aw  = p.applied_worked == null ? 0 : p.applied_worked
  LET af  = p.applied_failed == null ? 0 : p.applied_failed
  LET succ = (aw + af) == 0 ? 1 : aw / (aw + af)
  LET score = rel * (1 + 0.15*imp + 0.10*rec + 0.05*use) * (0.6 + 0.4*succ)
  SORT score DESC LIMIT @lim
  RETURN p._key
"""

_HYBRID_GRAPH_AQL = """
LET vec = (FOR p IN @@coll
             SORT APPROX_NEAR_COSINE(p.embedding, @qvec) DESC LIMIT 25 RETURN p._key)
LET bm  = (FOR p IN @@view
             SEARCH ANALYZER(
               p.problem_description IN TOKENS(@q,"text_en")
               OR p.solution_summary IN TOKENS(@q,"text_en")
               OR p.tags            IN TOKENS(@q,"text_en"), "text_en")
             SORT BM25(p) DESC LIMIT 25 RETURN p._key)
LET seeds = SLICE(vec, 0, 5)
LET nbrs = UNIQUE(FLATTEN(
  FOR s IN seeds
    RETURN (FOR n IN 1..1 ANY DOCUMENT(@@coll, s) pattern_relates_to RETURN n._key)))
LET fused = (FOR k IN UNIQUE(APPEND(APPEND(vec, bm), nbrs))
  LET vr = POSITION(vec, k, true)
  LET br = POSITION(bm,  k, true)
  LET graphOnly = (vr == -1 AND br == -1 AND POSITION(nbrs, k, true) != -1)
  RETURN { k, rrf: (vr == -1 ? 0 : 1.0/(10+vr+1)) + (br == -1 ? 0 : 1.0/(10+br+1))
                   + (graphOnly ? 1.0/(10+30) : 0) })
LET maxRrf = MAX(fused[*].rrf)
FOR f IN fused
  LET p = DOCUMENT(@@coll, f.k)
  FILTER p.superseded != true
  LET rel = f.rrf / (maxRrf > 0 ? maxRrf : 1)
  LET imp = (p.importance == null ? 5 : p.importance) / 10.0
  LET rec = POW(0.995, DATE_DIFF(p.last_used == null ? p.created_at : p.last_used, DATE_NOW(), "d"))
  LET use = LOG(1 + (p.usage_count == null ? 0 : p.usage_count)) / LOG(11)
  LET aw  = p.applied_worked == null ? 0 : p.applied_worked
  LET af  = p.applied_failed == null ? 0 : p.applied_failed
  LET succ = (aw + af) == 0 ? 1 : aw / (aw + af)
  LET score = rel * (1 + 0.15*imp + 0.10*rec + 0.05*use) * (0.6 + 0.4*succ)
  SORT score DESC LIMIT @lim
  RETURN p._key
"""


# ------------------------------------------------------------------ scoring
def first_hit_rank(returned: list[str], expected: list[str]):
    """1-based rank of the first acceptable key, or None if absent (any-of)."""
    for i, k in enumerate(returned, 1):
        if k in expected:
            return i
    return None


def metrics(ranks: list) -> dict:
    n = len(ranks) or 1
    return {
        "recall@1": round(sum(1 for r in ranks if r and r <= 1) / n, 3),
        "recall@3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
        "recall@5": round(sum(1 for r in ranks if r and r <= 5) / n, 3),
        "mrr": round(sum(1.0 / r for r in ranks if r) / n, 3),
    }


def main() -> int:
    hosts = [h.strip() for h in resolve("ARANGO_HOSTS", "http://localhost:8539").split(",") if h.strip()]
    username = resolve("ARANGO_ROOT_USERNAME", "root")
    verify_ssl = resolve("ARANGO_VERIFY_SSL", "true").lower() not in ("0", "false", "no", "off", "")
    db = ArangoClient(hosts=hosts, request_timeout=120, verify_override=verify_ssl).db(
        resolve("ARANGO_DEFAULT_DB_NAME", "memory"),
        username=username, password=resolve("ARANGO_ROOT_PASSWORD", ""))

    golden = json.load(open(GOLDEN))["queries"]
    print(f"Retrieval eval — {hosts} db={db.name!r} user={username!r}  "
          f"golden={len(golden)} queries{'  [DRY RUN]' if DRY_RUN else ''}")

    # 1. Sync golden file -> eval_queries (idempotent; keys are stable).
    for name in ("eval_queries", "eval_runs"):
        if not db.has_collection(name):
            if DRY_RUN:
                print(f"  would create collection {name!r}")
            else:
                db.create_collection(name)
    if not NO_SYNC and not DRY_RUN:
        eq = db.collection("eval_queries")
        for q in golden:
            doc = {**q, "created_by": username, "synced_at": now_iso()}
            existing = eq.get(q["_key"])
            if existing:
                doc["created_by"] = existing.get("created_by", username)
            eq.insert(doc, overwrite=True)
        print(f"  synced {len(golden)} golden queries -> eval_queries")

    # Validate expected keys actually exist (a renamed/deleted pattern makes
    # an eval query silently unwinnable — fail loud instead).
    all_keys = set(db.aql.execute("FOR p IN shared_patterns RETURN p._key"))
    dangling = [(q["_key"], k) for q in golden for k in q["expected"] if k not in all_keys]
    if dangling:
        for qk, k in dangling:
            print(f"  WARN {qk}: expected key {k!r} not in shared_patterns")

    corpus = len(all_keys)

    # 2. Decide modes.
    coll = db.collection("shared_patterns")
    has_vec = any(ix.get("type") == "vector" for ix in coll.indexes())
    api_key = resolve("OPENAI_API_KEY")
    model = resolve("EMBEDDING_MODEL", "text-embedding-3-small")
    modes = ["bm25"]
    if has_vec and api_key:
        modes += ["hybrid", "hybrid+graph"]
    else:
        print(f"  WARN hybrid modes skipped (vector index: {has_vec}, api key: {bool(api_key)})")
    if DRY_RUN:
        print(f"  would run modes {modes} over {len(golden)} queries (top-{TOP_K})")
        return 0

    # 3. Embed all queries in one batch (hybrid modes only).
    qvecs = {}
    if len(modes) > 1:
        vecs = embed([q["query"] for q in golden], model, api_key)
        qvecs = {q["_key"]: v for q, v in zip(golden, vecs)}

    # 4. Run + score.
    run = {"run_at": now_iso(), "ran_by": username, "corpus_size": corpus,
           "n_queries": len(golden), "top_k": TOP_K, "modes": {}, "by_category": {},
           "misses": {}}
    for mode in modes:
        ranks, by_cat, misses = [], {}, []
        for q in golden:
            if mode == "bm25":
                rows = list(db.aql.execute(_BM25_AQL, bind_vars={
                    "q": q["query"], "lim": TOP_K, "@view": "patterns_search"}))
            else:
                aql = _HYBRID_GRAPH_AQL if mode == "hybrid+graph" else _HYBRID_AQL
                rows = list(db.aql.execute(aql, bind_vars={
                    "q": q["query"], "qvec": qvecs[q["_key"]], "lim": TOP_K,
                    "@coll": "shared_patterns", "@view": "patterns_search"}))
            r = first_hit_rank(rows, q["expected"])
            ranks.append(r)
            by_cat.setdefault(q["category"], []).append(r)
            if r is None or r > 5:
                misses.append(q["_key"])
        run["modes"][mode] = metrics(ranks)
        run["by_category"][mode] = {c: metrics(rs) for c, rs in sorted(by_cat.items())}
        run["misses"][mode] = misses

    db.collection("eval_runs").insert(run)

    # 5. Report.
    print(f"\n  corpus: {corpus} patterns   queries: {len(golden)}   top-k: {TOP_K}")
    print(f"  {'mode':<14} {'R@1':>5} {'R@3':>5} {'R@5':>5} {'MRR':>6}")
    for mode in modes:
        m = run["modes"][mode]
        print(f"  {mode:<14} {m['recall@1']:>5.2f} {m['recall@3']:>5.2f} "
              f"{m['recall@5']:>5.2f} {m['mrr']:>6.3f}")
    best = max(modes, key=lambda m: (run["modes"][m]["mrr"]))
    print(f"\n  by category ({best}):")
    for cat, m in run["by_category"][best].items():
        print(f"    {cat:<12} R@1={m['recall@1']:.2f}  R@5={m['recall@5']:.2f}  MRR={m['mrr']:.3f}")
    worst = run["misses"][best]
    if worst:
        print(f"  misses in top-5 ({best}): {', '.join(worst)}")
    else:
        print(f"  no top-5 misses in {best} mode")
    print("\n  history: FOR r IN eval_runs SORT r.run_at RETURN "
          "{at: r.run_at, modes: r.modes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
