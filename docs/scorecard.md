# System Scorecard — arango-shared-memory

_Last updated: 2026-08-05 (round 2: P1/P2 addressed, incl. server repo; MCP server reloaded and
the new `memory_type` filter verified live from Cursor — valid types filter, invalid rejected by
validation; full read/write/delete round-trip confirmed end-to-end). Method: static review of
`templates/`, `scripts/`, `docs/PRD.md`, tests, the `arango-solutions-mcp-server` memory tools,
plus live `migrate.py` + `verify.py` + MCP tool calls against the shared prod cluster
(`prod.demo.pilot.arango.ai`, db `memory`, ArangoDB 3.12.9-1)._

## Overall: **A− / healthy; the ceiling is now adoption, not code or coverage**

Verification is all-green in production. The correctness/robustness backlog is cleared, the
server-side retrieval items turned out to be already shipped (plus a new `memory_type`
filter), and the P2 hardening is done. The one durable gap is **reuse**: writes flow and
ranking is excellent, but the surfaced→applied funnel and search adoption are thin and
concentrated. That is behavioral — it moves with usage and triage, not commits.

| Dimension | Grade | Δ | One-line |
|---|---|---|---|
| PRD requirement coverage | A− | ↑ | §3 retrieval now fully IMPLEMENTED (server hard-excludes superseded; memory_type first-class + filter) |
| Live health (prod) | A− | = | verify.py all-green |
| Adoption & value (read-path) | C+ | = | 25% surfaced→applied, usage concentrated — the real ceiling |
| Engineering quality | A− | = | 47 DB-free tests here + 27 server tests pass; eval-drift guard added |
| Reliability / robustness | A− | ↑ from B+ | graph guards, provenance, capture breadth, memory_type validation, eval guard |

## Changes applied

### Round 1 (correctness + hygiene)
- Live `migrate.py` (m003 memory_type / m004 weights / m006 temporal) → verify.py all-green.
- `phase3_lifecycle.py` / `phase2b_extract.py` graph guards; `maintain.py` provenance
  early-return; `verify.py` reporting nesting; `setup_schema.py` 3-tier + `memory` default;
  stale `arangodb-mcp` label; `check_evidence.py` line-local term match; drift matcher
  broadened; `pattern-applied` made MANDATORY in protocol; PRD REQ-071 reconciled.

### Round 2 (P1 server-side + P2 hardening)
- **`arango-solutions-mcp-server`:** two of the three "server-side" P1 items were **already
  shipped** — `pattern-search` hard-excludes `superseded == true` in all three ranking AQLs,
  and `save-pattern` accepts `memory_type` / `why` / `how_to_apply` first-class. Added the
  missing piece: an optional **`memory_type` filter** parameter on `pattern-search`
  (validated; ANDed onto the validity filter in every mode) + 2 unit tests (27 server tests pass).
- **Eval-drift guard:** `tests/test_eval_aql_sync.py` fails if `eval_retrieval.py`'s salience
  formula diverges from the server's `pattern-search` (skips when the server repo is absent).
  Currently in sync.
- **Capture miner broadened:** `capture_candidates.py` now mines resolved failures for MCP
  tools (e.g. `execute-aql-query`, `pattern-search`), not just Bash — while still skipping
  Edit/Write/Read churn; `ERROR_MARKER_RE` recognizes the MCP error envelope. +2 tests.
- **Cursor enforcement decision:** documented — keep `workflow.mdc` as the baseline; adding
  Cursor `hooks.json` enforcement (afterFileEdit / stop / beforeSubmitPrompt) is recorded as a
  tracked future enhancement (its own I/O protocol + test round).
- **Doc reconciliation:** `implementation-plan.md` "server-side changes" moved from
  Out-of-scope to SHIPPED.

## 1. PRD requirement coverage

All 14 sections IMPLEMENTED. §3 (retrieval & ranking) — previously PARTIAL pending
server-side work — is now IMPLEMENTED: hybrid+graph live, superseded hard-excluded
server-side, `memory_type`/`why`/`how_to_apply` first-class on save, `memory_type` filter on
search, `co_applied` edge learning + re-embed-on-edit via skills/maintenance.

## 2. Live health (prod)

`verify.py`: ALL CHECKS PASSED (7 collections, indexes, schema validation, round-trip,
`patterns_search` view, `memory_graph` + 8 edge defs, vector + TTL indexes, `memory_type` on
all 63 patterns). Backlogs (usage/ops, not health): ~93 open drift alerts, 56 unprocessed
observations, 1 proposed PRD patch.

## 3. Adoption & value — the read-path (unchanged; the ceiling)

63 patterns, 21 projects. Applied 9/63 (12 applies). Surfaced 36/63. **Surfaced→applied
25%.** 32 searches, 0.38 applies/search, usage concentrated (aws-ontology=10, r2g=5,
project-sentinel=4; ~12 projects at 0). Retrieval eval MRR 0.98 / R@5 1.00 across modes —
ranking is not the problem.

## 4. Remaining risks (small)

- **Cursor enforcement** is honor-system (decision recorded); mechanical `hooks.json`
  enforcement is a tracked future feature, not built.
- **Automatic `pattern-applied` capture** — still agent-driven (now MANDATORY in protocol);
  a server/hook-driven capture is the fallback if the funnel stays low.
- Capture miner correction-detection is still regex-narrow (by design).

## 5. Recommended next actions

**Code (P1/P2) — done this round.** Nothing further required in either repo for the
scorecard items.

**Reload — DONE.** MCP server reloaded; the `pattern-search` `memory_type` filter is verified
live (valid types filter; invalid types rejected by the new validation). No restart pending.

**Operational (the actual lever now — not code):**
- Move the surfaced→applied number: use the `memory_type` filter + MANDATORY `pattern-applied`;
  re-check `verify.py` over the coming weeks.
- Drive search in the ~12 zero-search projects (confirm the SessionStart digest fires there).
- Triage the backlog: 56 unprocessed observations, 91 open drift alerts, 1 proposed PRD patch.

**Optional future features (tracked):**
- Cursor `hooks.json` enforcement to match the Claude hooks.
- Server/hook-driven automatic `pattern-applied` capture if reuse stays under-reported.
