# Shared-Memory Enhancement Proposal — Graph + Vector Retrieval

**Status:** Draft for review · **Date:** 2026-07-01
**Basis:** SOTA deep-research pass (25 claims verified 3-vote adversarial, 0 killed). Sources cited inline.

---

## TL;DR

We run on ArangoDB (multi-model: document + graph + vector + BM25/ArangoSearch) but use it as a
flat document store with **exact keyword/tag match** retrieval. SOTA agent memory has converged on
**temporal knowledge graphs with hybrid retrieval** (vector + BM25 + graph traversal, reranked) and
**graded salience scoring** (recency · importance · relevance) instead of a static flag.

**Recommendation: enhance, but sequence by ROI — and do NOT lead with the graph.**
The highest-leverage, lowest-effort win is **Phase 1: embeddings + hybrid vector+BM25 retrieval +
graded scoring**. The graph layer (Phase 2) and temporal/decay logic (Phase 3) come after, because
the evidence that graph structure *itself* improves recall is mixed and it adds real cost.

> **Honest caveat up front:** every headline benchmark (Zep, Mem0) is *vendor self-reported*;
> independent replications came in materially lower and the vendors dispute each other's methodology.
> Mem0's own numbers show its **graph variant is ~3× slower, ~2× tokens, and loses on single/multi-hop**
> vs. plain vector Mem0 [arXiv:2504.19413]. So "add a graph" is not unconditionally correct — prove the
> cheap hybrid win first.

---

## What SOTA looks like (verified findings)

| # | Finding | Confidence | Source |
|---|---------|-----------|--------|
| 1 | Temporal knowledge graphs are the dominant architecture; **bi-temporal edges** (`t_valid`/`t_invalid` + `t_created`/`t_expired`), contradictions **invalidate** edges rather than delete | high | Zep/Graphiti, arXiv:2501.13956 |
| 2 | Three-tier model: **episodic** (raw, lossless) → **semantic entity** (extracted/resolved) → **community** (clusters); episodic vs semantic separation is considered essential | high | arXiv:2501.13956, 2502.06975 |
| 3 | Retrieval = **cosine vector + BM25 full-text + n-hop graph BFS**, fused with **RRF / MMR / graph reranker** | high | arXiv:2501.13956, 2602.06052 |
| 4 | Pipeline is multi-stage: **metadata preselect (recency/scope/type) → vector → graph-expand → rerank/budget** | high | arXiv:2602.06052 |
| 5 | Scoring = **recency (exp decay γ=0.995) + LLM importance (1–10) + relevance**, equal weights — replaces `worked:bool` | high | Generative Agents, arXiv:2304.03442 |
| 6 | Store **structured notes** with keywords/tags/context + **dynamic inter-memory links**; new memories **evolve** older ones (Zettelkasten) | high | A-MEM, arXiv:2502.12110 |
| 7 | **Mem0** = pragmatic extract-consolidate-retrieve; write-time reconciliation via **ADD/UPDATE/DELETE/NOOP** against top-k similar memories (built-in dedup) | high | arXiv:2504.19413 |
| 8 | Explicit lifecycle required: **summarization, selective retention, forgetting/decay, dedup, pruning** | high | arXiv:2602.06052 |
| 9 | Hybrid (vector + 1-hop graph via RRF) reported **≥12% context-precision lift** over dense-vector-only on an enterprise benchmark | medium | GraphRAG eval, arXiv:2507.03226 |
| 10 | RRF production default **k=60**; fuses on rank position, sidestepping score-scale mismatch | high | hybrid-search reference 2026 |

---

## Gap → fix mapping

| Current limitation | SOTA fix | ArangoDB capability (already available) |
|---|---|---|
| Tag/keyword exact match only | Semantic + full-text hybrid | `vector-search` (ANN cosine) + ArangoSearch BM25, fused by RRF |
| No relationships | Typed edges + traversal | edge collections + named graph + `graph-traverse`/`neighbors` |
| `worked:true` static bool | recency·importance·relevance score + usage feedback | AQL scoring over doc attrs; TTL index for decay |
| No dedup / near-duplicate merge | write-time ADD/UPDATE/MERGE/NOOP | vector-search on save + upsert/edge logic |
| No staleness handling | bi-temporal validity + decay/prune | validity attrs + ttl index + `supersedes` edges |

---

## Phase 1 — Hybrid retrieval + graded scoring  *(highest ROI, no graph yet)*

**Schema changes to `shared_patterns`:**
- `embedding`: `float[]` — embed `problem_description + "\n" + solution_summary` (+ tags).
- Replace `worked: bool` with:
  - `importance`: int 1–10 (LLM-rated at save, per Generative Agents),
  - `usage_count`: int (incremented when a pattern is surfaced & used),
  - `last_used`: ISO timestamp,
  - keep `created_at` for recency.

**Indexes / views:**
- Vector index (cosine) on `embedding`.
- ArangoSearch view over `problem_description`, `solution_summary`, `tags` with a `text` analyzer (stemming + norm) for BM25.

**`/pattern-search` rewrite (sketch — validate AQL against 3.12.4):**
```aql
// 1. vector candidates (semantic)
LET vec = (FOR p IN shared_patterns
  LET s = APPROX_NEAR_COSINE(p.embedding, @qvec)
  SORT s DESC LIMIT 20 RETURN {key: p._key, rank: LENGTH(...) })
// 2. BM25 candidates (lexical) via ArangoSearch view
LET bm = (FOR p IN patterns_view
  SEARCH ANALYZER(p.problem_description IN TOKENS(@q, 'text_en'), 'text_en')
  SORT BM25(p) DESC LIMIT 20 RETURN p._key)
// 3. RRF fuse (k=60), then re-score with recency+importance+relevance
```
Then final score:
`score = relevance + importance/10 + 0.995^(days_since(last_used))`  (equal-weight start; tune later).

**`/pattern-save` change:** compute embedding, LLM-rate `importance`, init `usage_count=0`.

**Effort:** low. **Impact:** high — this alone fixes the "memory never gets reused because the tag didn't match" failure. **Ship and measure before Phase 2.**

---

## Phase 2 — Graph layer

**Edge collections (+ one named graph `memory_graph`):**
- `pattern_addresses_requirement`: `shared_patterns → drift_alerts` (join on `req_id` / project).
- `pattern_from_project`: `shared_patterns → project_registry`.
- `pattern_relates_to`: `shared_patterns ↔ shared_patterns` (A-MEM-style semantic link, created at save via vector-neighbor lookup).
- `pattern_supersedes`: `shared_patterns → shared_patterns` (newer replaces older).
- `requirement_depends_on`: `drift_alerts ↔ drift_alerts`.

**Retrieval upgrade:** after Phase-1 vector entry nodes, `graph-traverse` 1–2 hops over `pattern_relates_to` / `pattern_addresses_requirement`, then rerank the expanded set. Enables "patterns that solved *related* requirements in *other* projects."

**Effort:** medium. **Impact:** medium, corpus-dependent (see caveat). Gate on a measured lift over Phase 1.

---

## Phase 3 — Temporal validity + lifecycle

- **Bi-temporal attrs** on patterns/edges (`t_valid`, `t_invalid`, `t_created`, `t_expired`); on contradiction, set `t_invalid` + a `supersedes` edge instead of deleting (preserves history, Zep-style).
- **Dedup/merge at save** (Mem0-style): vector-search for near-duplicates above a cosine threshold → ADD / UPDATE / MERGE / NOOP.
- **Decay & pruning:** recency exp-decay in scoring; TTL index + importance-threshold to prune stale `drift_alerts` and low-value patterns — without evicting reusable cross-project knowledge.

**Effort:** medium-high. **Impact:** quality/hygiene over time.

---

## Trade-offs & open questions (must resolve before building)

1. **Does graph traversal actually help *this* corpus** (cross-project solved-problem patterns), given vendor data shows graph variants can *hurt* single/multi-hop while adding latency/tokens? → Decide empirically after Phase 1.
2. **Entity/link + importance population:** LLM-per-save (Graphiti/A-MEM quality, higher cost/latency) vs. cheap heuristics on existing fields (`project_id`, `req_id`, `tags`)? Start heuristic, add LLM if needed.
3. **Dedup/forget policy:** what cosine threshold = "near-duplicate to merge"? What decay/TTL prunes stale drift without losing knowledge?
4. **Embeddings ops in the MCP server:** which model, on-write vs batch, re-embed on edit, vector dims/metric/nLists for expected corpus size.

---

## Sources
- Zep / Graphiti — arXiv:2501.13956
- Agent-memory survey (2026) — arXiv:2602.06052
- Generative Agents — arXiv:2304.03442
- A-MEM — arXiv:2502.12110
- Mem0 / Mem0g — arXiv:2504.19413
- MemGPT / Letta — arXiv:2310.08560
- Episodic memory position paper — arXiv:2502.06975
- GraphRAG hybrid eval — arXiv:2507.03226
- ArangoDB vector search / GraphRAG docs — docs.arangodb.com/3.13/data-science/graphrag
