# Phase 2 — Graph Layer (implemented)

**Status:** Foundation built & validated live · graph-expanded search coded, awaiting one MCP reload
**Date:** 2026-07-05 · **Parent:** [shared-memory-enhancement-proposal.md](shared-memory-enhancement-proposal.md)

## What Phase 2 adds
On top of Phase 1 hybrid retrieval, model relationships between memories as a graph and use them to
expand retrieval — surface patterns related to the top hits even when they don't match the query
directly.

## Built
- **Named graph `memory_graph`** with three edge collections:
  - `pattern_relates_to` (shared_patterns ↔ shared_patterns) — semantic KNN links.
  - `pattern_supersedes` (shared_patterns → shared_patterns) — reserved for Phase 3 (newer contradicts older).
  - `pattern_from_project` (shared_patterns → project_registry) — provenance.
- **`scripts/phase2_setup.py`** — idempotent; creates the graph and populates edges from existing data
  (no LLM, no new API calls):
  - `pattern_relates_to`: for each embedded pattern, links its top-3 nearest neighbours with cosine ≥ 0.30
    (deterministic edge `_key`s → safe re-runs). *Validated: 6 edges over 5 patterns; the Docker/vector
    deployment pattern linked cross-project to the jlr ArangoDB-vector-index-gotchas pattern at sim 0.48.*
  - `pattern_from_project`: provenance edge where the project is registered. *2 edges.*
- **`pattern-search` graph expansion** (`mcp_tools/pattern_memory_tools.py`): new `graph_expand=True`
  param. After the hybrid vector⊕BM25 candidates, it pulls 1-hop `pattern_relates_to` neighbours of the
  top-5 semantic seeds into the pool (graph-only nodes get a small RRF floor, then the graded scoring
  decides rank). Reports `mode: "hybrid+graph"`. Guarded: falls back to plain hybrid if the edge
  collection is absent.

## Note on APPROX_NEAR_COSINE
`APPROX_NEAR_COSINE` must appear once, bound via `LET` and used in `SORT` — a second direct call (e.g.
in `RETURN`) breaks the vector-index optimizer with `ERR 1554 failed vector search`. Compute once:
`LET s = APPROX_NEAR_COSINE(q.embedding, @vec) SORT s DESC ... RETURN {..., s}`.

## To activate
Reload the MCP connection once (also activates the embedding-retry added to `generate_embeddings`).
Then verify: `pattern-search` on an ArangoDB-vector-index query should report `mode: "hybrid+graph"`
and may include a result flagged `via_graph: true`.

## Next (Phase 2b / Phase 3, not built)
- LLM-extracted edges (`LLM_EXTRACTION_MODEL=gpt-4o` in .env): `pattern_addresses_requirement`
  (pattern → drift_alerts), `requirement_depends_on`.
- `pattern_supersedes` population via contradiction/dedup detection at save time; recency/importance
  decay + TTL pruning (Phase 3 lifecycle).
