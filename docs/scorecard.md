# System Scorecard — arango-shared-memory

_Last updated: 2026-08-05 (round 3: operational backlog triage + Cursor/Claude hook enforcement;
28 local bootstrapped projects refreshed; SessionStart verified in all 9 locally available
zero-search registry projects; apply-attribution gate shipped). Method: static review of
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
| Adoption & value (read-path) | B− | ↑ | 29% surfaced→applied; automatic recall + apply attribution now instrumented |
| Engineering quality | A− | = | 55 passing + 2 skipped DB-free tests here; 27 server tests passed in round 2 |
| Reliability / robustness | A | ↑ | Cursor/Claude parity, merge-safe rollout, separate automatic-recall telemetry |

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

### Round 3 (operational leverage + enforcement)
- **Cursor-native parity:** project hooks now inject the shared-memory SessionStart digest,
  queue code/PRD edits, and issue one Stop follow-up for unresolved drift or missing apply
  attribution. The rollout merges by event/command and preserves unrelated Cursor hooks.
- **Honest apply capture:** Cursor and Claude PostToolUse hooks track keys returned by
  `pattern-search`; `pattern-applied` clears only keys explicitly reported. The Stop gate asks
  once when attribution is incomplete. It never auto-applies all surfaced results.
- **Automatic recall telemetry:** successful SessionStart reads are logged with
  `mode: "session_recall"` and surfaced counters. `verify.py` reports these separately from
  interactive searches, preserving a meaningful apply/search denominator.
- **Rollout:** merge-refreshed 28 bootstrapped projects under `~/code`; a second dry run reported
  all 28 unchanged. Cursor digest wrappers were smoke-tested with telemetry disabled in
  `agentic-graph-analytics` and `FinReflectKG` (`domyn`).
- **Zero-search audit:** all 12 registry entries accounted for. Digest execution succeeded in
  all 9 with local checkouts. `brambles-pallet-network` and `feature-tracker` have no local
  checkout; stale `multi-tenant-time-travel-architecture` aligns with the already-registered
  `network-asset-management-demo`, not `multi-tenant-autograph`.

## 1. PRD requirement coverage

All 14 sections IMPLEMENTED. §3 (retrieval & ranking) — previously PARTIAL pending
server-side work — is now IMPLEMENTED: hybrid+graph live, superseded hard-excluded
server-side, `memory_type`/`why`/`how_to_apply` first-class on save, `memory_type` filter on
search, `co_applied` edge learning + re-embed-on-edit via skills/maintenance.

## 2. Live health (prod)

Prior `verify.py`: ALL CHECKS PASSED (7 collections, indexes, schema validation, round-trip,
`patterns_search` view, `memory_graph` + 8 edge defs, vector + TTL indexes, `memory_type` on
all 63 patterns). Current live backlog query: **92 open drift alerts** (67 PARTIAL, 25 MISSING),
**31 unprocessed observations**, 1 proposed PRD patch.

## 3. Adoption & value — the read-path (unchanged; the ceiling)

63 patterns, 21 projects. Applied 12/63 (15 applies). Surfaced 41/63. **Surfaced→applied
29%.** 39 interactive searches after the live `memory_type=feedback` verification call,
0.38 applies/search, and 12 registry projects had no logged interactive search before hook
rollout. Future SessionStart reads are now logged separately; smoke tests explicitly disabled
logging so the adoption number was not inflated. Retrieval eval remains MRR 0.98 / R@5 1.00
across modes — ranking is not the problem.

## 4. Remaining risks (small)

- **Apply truth still requires agent judgment.** Hooks now force one attribution pass, but
  deliberately cannot infer reuse from a search alone. This is the safe automation boundary.
- Three registry entries have no matching current checkout/identity and need registry cleanup
  before they can produce new project-local reads.
- Capture miner correction-detection is still regex-narrow (by design).

## 5. Recommended next actions

**Code (P1/P2) — done this round.** Nothing further required in either repo for the
scorecard items.

**Reload — DONE.** MCP server reloaded; the `pattern-search` `memory_type` filter is verified
live (valid types filter; invalid types rejected by the new validation). No restart pending.

**Operational work completed now:**
- `memory_type` guidance is explicit in templates/digests; apply attribution is mandatory and
  mechanically nudged in both clients.
- SessionStart was verified for every locally available zero-search project; future real starts
  produce distinct recall telemetry.
- Observation triage was classified: 10 promote (already represented by an alert/patch),
  1 duplicate, 20 acknowledge. The exact live bulk transition awaits database-write approval.
- Alert triage: 71/92 alerts are concentrated in six projects. Do not bulk-close them. Immediate
  review gates are `brambles-pallet-network/CAND-02` (evidence recommends re-close, but prior
  reopen makes this a user decision) and `arango-ontoextract/FR-19.4` (PRD patch decision).
- Proposed FR-19.4 patch review: the premise is verified — two adapter paths are deterministic or
  externally delegated and have no AOE-owned prompt. Recommendation: **accept with edited wording**
  that distinguishes LLM prompt injection from deterministic CQ-priority behavior without
  claiming the latter is already implemented.

**Next measurement:** run `scripts/verify.py` weekly for 4 weeks and compare interactive searches,
automatic recalls, surfaced→applied conversion, and zero-read projects. Scheduling mechanism is
left to the operator (maintenance launchd/cron or Cursor Automation); no schedule was silently
installed.
