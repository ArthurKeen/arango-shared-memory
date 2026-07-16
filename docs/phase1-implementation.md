# Phase 1 — Concrete Implementation Spec

**Status:** Ready to execute (1a) / blocked on prereqs (1b) · **Date:** 2026-07-02
**Parent:** [shared-memory-enhancement-proposal.md](shared-memory-enhancement-proposal.md)
**Validation:** Every DDL/AQL below was run against the live 3.12.4 instance in a throwaway
`phase1_probe` database (since deleted). Results noted inline.

---

## Live-instance findings that reshaped Phase 1

| Capability | Status on this instance | Consequence |
|---|---|---|
| ArangoSearch view + `text_en` + `BM25()` | ✅ works, no flag | Phase 1a ships today |
| Graded scoring AQL (recency/importance/usage) | ✅ works | Phase 1a ships today |
| Vector (ANN) index | ❌ `vector index feature is not enabled. Run ArangoDB with --experimental-vector-index` | Phase 1b blocked |
| Embedding generation in MCP server | ❌ none — `vector-search` takes a caller-supplied `query_vector` | Phase 1b needs an embedding source |

**Therefore Phase 1 splits:**
- **Phase 1a** — BM25 relevance + graded scoring. No flag, no embeddings, no external API. **Do this now.**
- **Phase 1b** — vector + hybrid (RRF) fusion. Gated on two prerequisites (below). **Defer.**

---

## Phase 1a — BM25 + graded scoring (ship now)

### Schema additions to `shared_patterns`
Keep all existing fields. Add three (backfilled by `scripts/phase1_setup.py`):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `importance` | int 1–10 | 5 | LLM-rated salience at save (Generative Agents poignancy) |
| `usage_count` | int | 0 | incremented each time the pattern is surfaced **and used** |
| `last_used` | ISO string | `created_at` | drives recency decay |

`worked:bool` is retained (it's a distinct signal: "did this solution actually work"), but it no
longer drives ranking — `importance` does.

### Setup (idempotent)
```bash
cd ~/code/arango-solutions-mcp-server
poetry run python ~/code/arango-shared-memory/scripts/phase1_setup.py --dry-run   # preview
poetry run python ~/code/arango-shared-memory/scripts/phase1_setup.py             # apply
```
This backfills the three fields and creates the `patterns_search` ArangoSearch view over
`problem_description + solution_summary + tags` with the built-in `text_en` analyzer
(stemming + frequency + norm).

### Rewritten `/pattern-search` retrieval (validated AQL)
Replaces `FILTER "x" IN p.tags`. BM25 relevance, normalized within the candidate window, combined
with importance + recency-decay + usage. Bind `@q` = the user's problem description.

```aql
LET candidates = (
  FOR p IN patterns_search
    SEARCH ANALYZER(
      p.problem_description IN TOKENS(@q, "text_en")
      OR p.solution_summary IN TOKENS(@q, "text_en")
      OR p.tags            IN TOKENS(@q, "text_en"), "text_en")
    LET rel = BM25(p)
    SORT rel DESC
    LIMIT 25
    RETURN { p, rel }
)
LET maxRel = MAX(candidates[*].rel)
FOR c IN candidates
  LET relN = c.rel / (maxRel > 0 ? maxRel : 1)                              // [0,1]
  LET imp  = (c.p.importance == null ? 5 : c.p.importance) / 10.0           // [0,1]
  LET rec  = POW(0.995, DATE_DIFF(c.p.last_used == null ? c.p.created_at : c.p.last_used,
                                  DATE_NOW(), "d"))                          // exp decay γ=0.995
  LET use  = LOG(1 + (c.p.usage_count == null ? 0 : c.p.usage_count)) / LOG(11)  // saturates ~10 uses
  LET score = relN + imp + rec + use                                        // equal weights (tune later)
  SORT score DESC
  LIMIT 8
  RETURN {
    _key: c.p._key, project_id: c.p.project_id,
    problem_description: c.p.problem_description,
    solution_summary: c.p.solution_summary,
    tags: c.p.tags, score, relevance: relN, importance: imp, recency: rec, usage: use
  }
```
> Probe result: for query "problem 7", the exact match scored 5.70 vs ~3.06 for non-matches — correct ranking.
> Weights start equal per Generative Agents (arXiv:2304.03442); tune once real query logs exist.

### `/pattern-search` usage feedback (new step)
After the agent **uses** a returned pattern, bump its reinforcement signal:
```aql
FOR p IN shared_patterns FILTER p._key == @key
  UPDATE p WITH { usage_count: p.usage_count + 1, last_used: DATE_ISO8601(DATE_NOW()) } IN shared_patterns
```
Call `upsert-document` / `update-document` with `usage_count += 1`, `last_used = now`.

### Rewritten `/pattern-save` (Phase 1a additions)
On save, the skill now also sets:
- `importance`: ask the model to rate 1–10 ("1 mundane … 10 broadly reusable / high-impact"),
- `usage_count`: 0,
- `last_used`: `<ISO now>`.

Concrete `upsert-document` call (real tool signature — matches the Phase-4 fix from earlier):
```
collection_name: "shared_patterns"
search_fields:  { "_key": "<PROJECT_ID>_<category>_<YYYYMMDD_HHMMSS>" }
document_data:  { ...existing fields..., "importance": <1-10>, "usage_count": 0, "last_used": "<ISO now>" }
```

### verify.py additions (optional, recommended)
Add a check that the `patterns_search` view exists and that `shared_patterns` docs carry the three
new fields — so the adoption snapshot reflects Phase 1a readiness.

---

## Phase 1b — vector + hybrid (RRF)  *(IMPLEMENTED — awaiting API key to activate)*

### Prerequisites
1. ✅ **Vector index enabled.** `shared-memory-arangodb` container recreated with
   `arangod --experimental-vector-index` (note: the flag is `--experimental-vector-index`, not the
   `--vector-index` the server README mentions). Verified: vector index + `APPROX_NEAR_COSINE` work.
   Data preserved via the named volume `shared-memory-arango-data`.
2. ✅ **Embedding source chosen: OpenAI, server-side.** New `embed-text` MCP tool
   (`arango-solutions-mcp-server/mcp_tools/embedding_tools.py`, registered in `server.py`) calls the OpenAI
   embeddings API via httpx. Model `text-embedding-3-small` (1536 dims), override with `EMBEDDING_MODEL`.

### What's built
- **Server:** `embed-text` tool — `texts: [str]` → `{model, dimension, embeddings: [[...]]}`.
- **Setup:** `scripts/phase1b_setup.py` — backfills `embedding` on existing patterns and creates the
  cosine vector index (`dimension` 1536, `nLists ≈ 15·√N`). Skips index creation until ≥1 embedded doc.
- **Skills (templates, NOT yet propagated):** `pattern-save` Phase 2b embeds on write;
  `pattern-search` Phase 1b does hybrid RRF (vector ⊕ BM25, k=60) then the 1a boosts.
  Hybrid AQL syntax-validated against the live instance.

### Activation runbook (requires the OpenAI key — I can't do these without it)
1. Add to the `arangodb-memory-mcp` env in **both** `~/.cursor/mcp.json` and `~/.claude.json`:
   ```json
   "OPENAI_API_KEY": "sk-...",
   "EMBEDDING_MODEL": "text-embedding-3-small"
   ```
2. Restart the MCP server (reload the Cursor / Claude Code MCP connection) so `embed-text` loads.
3. Smoke-test the tool: call `embed-text` with `texts: ["hello world"]` → expect `dimension: 1536`.
4. Run the backfill + index:
   ```bash
   cd ~/code/arango-solutions-mcp-server
   poetry run python ~/code/arango-shared-memory/scripts/phase1b_setup.py --dry-run
   poetry run python ~/code/arango-shared-memory/scripts/phase1b_setup.py
   ```
5. Validate a hybrid `/pattern-search` returns sensible ranking, THEN propagate the two updated
   skills to the 25 projects (same mechanism as 1a — deliberately held until tested with real embeddings).

### RRF fusion (why, not raw-score addition)
BM25 and cosine are not on the same scale, so results are fused on **rank position**:
`rrf(d) = Σ_lists 1 / (60 + rank_in_list(d))`, normalized to [0,1], then combined equally with
importance + recency + usage — same graded formula as 1a.

---

## Rollout, cost, rollback

- **Rollout:** run `phase1_setup.py` on `memory` → update the two skill templates → validate with a
  few real `/pattern-search` queries → **then** propagate skills to the 25 bootstrapped projects
  (same mechanism used for the hook fix). Do not propagate before validating, so behavior changes
  land in one reviewed step.
- **Cost:** Phase 1a adds ~0 latency (BM25 + one AQL pass; probe ran in ~2–7ms over 20 docs) and no
  external API calls. Storage: one view + three scalar fields.
- **Rollback:** drop the `patterns_search` view; the new fields are additive and can be ignored. The
  old tag-filter `/pattern-search` still works against the unchanged collection.

## Open questions carried from the proposal (resolve before 1b)
1. Embedding model + dimension + on-write vs batch (drives the vector index params).
2. Whether graph traversal (Phase 2) beats 1a hybrid **for this corpus** — measure first.
3. Dedup cosine threshold and decay/TTL pruning policy (Phase 3).
