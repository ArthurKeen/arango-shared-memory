# System Scorecard — arango-shared-memory

_Last updated: 2026-08-13 (round 4: the read-path came alive — automatic recall is producing
reads across 18 projects and applies grew 7×; a new MCP liveness probe closes the blind spot
that hid three multi-day outages). Method: static review of `templates/`, `scripts/`,
`docs/PRD.md`, tests, the `arango-solutions-mcp-server` memory tools, plus live `migrate.py` +
`verify.py` + MCP tool calls against the shared prod cluster (`prod.demo.pilot.arango.ai`,
db `memory`, ArangoDB 3.12.9-1)._

## Overall: **A− / healthy; adoption is finally moving, but headcount is still one**

Verification is all-green and the read path — inert for its first six days — is now the
busiest part of the system: **116 automatic recalls across 18 projects, 86 apply events,
surfaced→applied 44%**. Two things keep this at A− rather than A. First, the apply numbers
are *gate-assisted* and should not be read as clean reuse evidence (§3). Second, and more
structurally: until this round the system **could not detect its own unavailability** — every
check talked to ArangoDB directly, so three separate total outages of the agent-facing path
reported `ALL CHECKS PASSED`. That is now probed, but it was a real blind spot for weeks.

| Dimension | Grade | Δ | One-line |
|---|---|---|---|
| PRD requirement coverage | A− | = | §3 retrieval fully IMPLEMENTED (superseded hard-excluded; memory_type first-class + filter) |
| Live health (prod) | A− | = | verify.py all-green |
| Adoption & value (read-path) | B | ↑ | 44% surfaced→applied, 86 applies, 116 recalls, 18 projects reading — but 100% one operator, and the apply signal is gate-assisted |
| Engineering quality | A− | = | 57 DB-free tests here (2 skipped); server suite green through the v3 platform spine |
| Reliability / robustness | A | = | Cursor/Claude parity, merge-safe rollout, separate recall telemetry |
| **Liveness / self-observability** | **C** | **new** | was **D**: three silent total outages reported healthy. `verify.py` now launches the configured MCP server and asserts the memory tools are exposed; still no continuous/scheduled probe |

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

### Round 4 (the read path came alive; liveness blind spot closed)

- **PR #1 (external contributor) fixed automatic recall.** Two AQL object keys named `desc` — a
  reserved word — made both digest queries a parse error, so the SessionStart digest had produced
  *nothing* since it shipped. Fail-open turned a total outage into silence. Recall went from 0 to
  **116 logged reads across 18 projects**; `domyn` alone has 14 recalls and 0 interactive searches,
  i.e. it is receiving memory it never asked for. This single fix is the largest driver of the
  round-4 numbers.
- **Two more silent breaks, same class, from the server's v3 platform spine:** the src-layout
  packaging removed the top-level `main.py` that every MCP client config launched, and
  `MCP_PROFILE` began defaulting to `readonly` — which drops all writes *and* the entire `memory`
  tool category. Both produce zero errors: the skills fail open, so tools simply vanish. Fixed in
  the local configs, documented in `setup.md`/`ONBOARDING.md` (config blocks now carry
  `arangodb-mcp` + `MCP_PROFILE=developer` + `MCP_TOOLSETS=graph,search`) and in the server
  `CHANGELOG` as breaking migration notes.
- **`verify.py` gained an MCP liveness + capability probe** (`check_mcp_liveness`). It launches the
  *configured* `arangodb-memory-mcp` server — same command, args and env an agent gets — completes
  an `initialize`/`tools/list` handshake, prints the resolved profile and toolsets, and FAILS when
  the four shared-memory tools are absent. Verified against all three real failure modes: healthy
  (61 tools, `developer` + `graph,search`), genuine `MCP_PROFILE=readonly` (17 tools → fails with
  the profile fix in the message), and a config launching the removed `main.py` (fails with the
  launcher's stderr). Skip with `--no-mcp`.

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

## 3. Adoption & value — the read-path (moving fast; read the caveats)

68 patterns, 21 projects. Applied **28/68 (86 applies)**. Surfaced 63/68. **Surfaced→applied
44%** (was 29%). **71 interactive searches** (hit rate 97%), **116 automatic SessionStart
recalls**, 1.21 applies per interactive search, **18 projects** now producing reads — 4 registry
projects still have none (`brambles-pallet-network`, `feature-tracker`, `iam`,
`multi-tenant-time-travel-architecture`). Retrieval eval unchanged at MRR 0.98 / R@5 1.00 across
modes — ranking has never been the problem.

**Two caveats that belong next to those numbers:**

1. **The apply signal is gate-assisted, not clean reuse evidence.** 1.21 applies per interactive
   search is >1, and outcomes are **75 worked / 0 failed** — a genuine reuse population produces
   some failures. The apply-attribution gate blocks session end until surfaced patterns are
   dispositioned, which creates pressure toward marking "applied". Treat 44% as directionally
   real but inflated; the honest read is "attribution is now happening", not "reuse quadrupled".
2. **Headcount is still one.** All 71 searches and all 86 applies are `arthur`. PJ has 1 saved
   pattern (the PR #1 fix) and zero searches. Volume grew; the multi-human evidence this system
   is meant to produce still does not exist. This remains the single most valuable open item and
   no commit can fix it.

## 4. Remaining risks (small)

- **Apply truth still requires agent judgment.** Hooks now force one attribution pass, but
  deliberately cannot infer reuse from a search alone. This is the safe automation boundary —
  and per §3 the gate biases the resulting number upward, which the metric must not hide.
- **Liveness is probed on demand, not continuously.** `verify.py` now catches a dead or
  under-profiled MCP server, but only when someone runs it. Every outage so far was found by a
  human noticing absence. Folding the probe into the scheduled maintenance run (or a
  SessionStart warning) would close the remaining window.
- Three registry entries have no matching current checkout/identity and need registry cleanup
  before they can produce new project-local reads.
- Capture miner correction-detection is still regex-narrow (by design).

## 5. Recommended next actions

**Ranked for round 5:**

1. **Get a second human reading and applying.** Two of the three weakest signals (single-operator
   concentration, unvalidatable apply data) resolve only this way. PJ has proven the value of a
   second pair of eyes on *code*; the read path has never had a second user.
2. **Make liveness continuous.** Fold `check_mcp_liveness` into the scheduled maintenance run so
   a dead/under-profiled server pages someone instead of waiting to be noticed.
3. **Validate the apply signal.** Look for negative outcomes — 75/0 worked/failed is not a
   credible distribution. If `pattern-applied` outcomes stay all-positive, the gate is producing
   compliance rather than truth, and the funnel metric needs a harder definition.
4. **Close the 4 zero-read registry projects** (or retire the stale entries).

**Code — done this round.** MCP liveness probe in `verify.py`; client-config corrections in
`setup.md` / `ONBOARDING.md`; breaking-change migration notes in the server `CHANGELOG`.

**Reload — DONE.** MCP server reloaded on the corrected entry point and `developer` profile;
61 tools exposed, all four memory tools verified live via the new probe.

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
