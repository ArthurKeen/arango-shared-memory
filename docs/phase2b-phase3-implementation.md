# Phase 2b + Phase 3 — LLM Edges & Lifecycle (implemented)

**Status:** Live & validated · **Date:** 2026-07-07
**Parent:** [phase2-implementation.md](phase2-implementation.md)

## Phase 2b — LLM-extracted edges (`scripts/phase2b_extract.py`, gpt-4o)
Adds edges the structural/similarity pass can't infer, using `LLM_EXTRACTION_MODEL` (gpt-4o) in JSON mode:

- **`pattern_addresses_requirement`** (shared_patterns → drift_alerts): one chat call per drift alert,
  patterns listed inline, LLM returns which directly resolve the requirement. **13 edges** built —
  e.g. the RAG-grounding + api-design patterns correctly map to the "hybrid retrieval <500 ms p95" NFR
  and the ArangoSearch BM25-view requirement.
- **`requirement_depends_on`** (drift_alerts → drift_alerts): one call over all requirements → dependency
  pairs. **2 edges**.

Bounded LLM cost (N_alerts + 1 calls), idempotent (deterministic edge `_key`s), edges tagged
`extracted_by`. Requires OPENAI_API_KEY.

## Phase 3 — lifecycle (`scripts/phase3_lifecycle.py`)
Three hygiene passes; flags/CLI: `--sim 0.90 --ttl-days 90 --stale-days 180`, `--dry-run`.

1. **Supersede** — near-duplicate patterns (cosine ≥ `--sim`): newer (by `created_at`) supersedes older →
   `pattern_supersedes` edge (new→old) + demote the old one (`superseded=true`, `superseded_by`,
   stash `importance_original`, set `importance=1`). Demotion rides the **existing** `/pattern-search`
   scoring (importance + recency), so **no server change / reload** is needed to sink superseded results.
   *Current run: 0 pairs — patterns are diverse.*
2. **TTL pruning** — TTL index on `drift_alerts.closed_at`, expiring CLOSED alerts `--ttl-days` after
   closure. Open alerts have no `closed_at` and never expire. **Patterns are never auto-deleted.**
   *Created: `ttl_closed_alerts` (90 d).*
3. **Stale report** — lists (never deletes) patterns with `importance ≤ 3 AND usage_count == 0 AND`
   age > `--stale-days`, for human review. *Current: 0 candidates.*

## Graph state after Phase 1–3
```
6 patterns · 16 drift_alerts
edges: relates_to 10 · from_project 3 · addresses_requirement 13 · depends_on 2 · supersedes 0
```

## Gotchas recorded
- AQL object key `desc:` collides with the `DESC` keyword — quote it: `"desc":`.
- TTL index uses `expireAfter` (seconds), not `expireAfterSeconds`, via python-arango `add_index`.
- (Earlier) `APPROX_NEAR_COSINE` must be bound via `LET` and used once in `SORT`; a second direct call → ERR 1554.

## Re-run cadence (all idempotent)
- After new patterns: `phase1b_setup.py` (embed + index) → `phase2_setup.py` (similarity edges) →
  `phase2b_extract.py` (LLM edges) → `phase3_lifecycle.py` (supersede/TTL/stale).
- Consider wiring these into a scheduled job once volume grows.

## Not built
- Auto-run of the graph passes on save (currently manual scripts).
- `pattern-search` hard-exclusion of superseded patterns (currently soft-demoted via importance).
- Server README/PRD flag fix: says `--vector-index`, real flag is `--experimental-vector-index`.
