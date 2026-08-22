# System Scorecard — arango-shared-memory

_Last updated: 2026-08-19 (round 5: the apply gate's inflation mechanism was found and removed —
it was mathematically unsatisfiable, so the only way to clear it was the dishonesty its own
message forbade. Engineering quality improved materially: CI, a license, and the test gap that
hid the defect. No post-fix telemetry exists yet). Method: static review of `templates/`,
`scripts/`, `docs/PRD.md`, tests, the `arango-solutions-mcp-server` memory tools, plus live
`verify.py` (all-green, incl. MCP liveness) and direct read-only AQL against the shared prod
cluster (`prod.demo.pilot.arango.ai`, db `memory`)._

## Overall: **A− / healthy and better-engineered; the value metric is now known to have been inflated**

Verification is all-green including MCP liveness (61 tools, `developer` profile), and volume keeps
growing: **127 automatic recalls across 20 projects, 80 interactive searches, 91 apply events**.

What changed this round is not a number but a *confidence level*. Round 4 recorded a suspicion —
that the apply funnel was "gate-assisted". That suspicion is now a diagnosed defect with a known
mechanism: the stop gate computed unresolved work as `surfaced − applied` with no third state, so
an 8-result search with 2 genuine reuses left 6 pending **forever**, while the gate's own message
correctly forbade marking every result as applied. Both rules could not hold. The only arithmetic
escape was the exact dishonesty the message prohibited, and the gate re-fired every turn — so it
read as nagging rather than broken, which is how it survived for weeks.

The corroborating evidence is now unambiguous: **80 recorded outcomes, 0 failures, and not one of
76 patterns has ever recorded a single failed application.** No genuine population of reused
solutions is 100% successful. That is compliance data, not outcome data.

The defect is fixed and rolled out (2026-08-18), but **every figure in §3 predates the fix**, so
this round's conversion numbers still carry the inflation. Round 6 is the first measurement that
can be trusted, and it should be expected to look *worse*.

| Dimension | Grade | Δ | One-line |
|---|---|---|---|
| PRD requirement coverage | A− | = | §3 retrieval fully IMPLEMENTED (superseded hard-excluded; memory_type first-class + filter) |
| Live health (prod) | A− | = | verify.py all-green incl. MCP liveness; two open warnings (1 deferred embedding, 7 patches awaiting review) |
| Adoption & value (read-path) | B− | ↓ | volume up (127 recalls, 80 searches, 91 applies, 20 projects reading) but conversion flat at 44%, still **one** human, and the inflation is now confirmed rather than suspected |
| **Metric integrity** | **C** | **new** | the apply funnel's inflation mechanism is identified and removed, but every historical figure was produced under it and no post-fix data exists yet |
| Engineering quality | A | ↑ | 66 DB-free tests (2 skipped, was 57); CI matrix on Python 3.11–3.14 all green; MIT licensed; the missing allow-path coverage that hid the gate defect is closed |
| Reliability / robustness | A | = | Cursor/Claude parity maintained through the gate fix (the Cursor gate carried the identical defect); merge-safe rollout to 31 repos; separate recall telemetry |
| Liveness / self-observability | B− | ↑ | **now continuous without a scheduler**: SessionStart fails open but no longer fails *silent*, so every session reports a dead read path. Server-side, a blocked startup connect no longer takes the whole toolset down (2026-08-21). Remaining gap: nothing reports when nobody starts a session |

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

### Round 5 (the gate that produced compliance; engineering quality)

- **The apply gate was unsatisfiable, and that is why the funnel was inflated.** `pending =
  surfaced − applied` with no third state. The missing concept is the *normal* outcome of any
  8-result search: **reviewed, and deliberately not reused**. Added `dismiss_surfaced.py`, which
  records `dismissed_keys` as **local audit state only** — it performs no write to
  `shared_patterns`, so recording "I looked and moved on" can never inflate `usage_count` or
  corrupt the success-rate ranking other people's retrieval depends on. Reuse remains attributable
  by exactly one mechanism (`pattern-applied`). It refuses three silently-wrong inputs: a key never
  surfaced, a key already applied, and a session with no state.
- **Fixed in both runtimes.** The Cursor gate (`shared_memory_stop_gate.py`) carried the identical
  arithmetic — 30/30 projects — and the original single-runtime diagnosis missed it. Because Cursor
  namespaces session state under `.cursor/` while Claude uses `.claude/`, the tool *locates* the
  session file rather than assuming a directory, so one copy serves both.
- **Both installers ship it with the gate.** `bootstrap_project.sh`'s place list and
  `rollout_cursor_hooks.py`'s `CLAUDE_HOOK_FILES` were both enumerated explicitly, so shipping the
  fixed gate alone would have pointed every project at a script that does not exist — strictly
  worse than the defect. Rolled out to 30 projects, verified byte-identical, `.pre-update.*`
  backups kept; `argos` picked it up through a clean bootstrap.
- **Both gate messages now name a runnable command** with the session id and pending keys filled
  in. The old text said "state that explicitly", which has no effect on the state file — a message
  that only says that leaves the gate unsatisfiable in practice, whatever the arithmetic does.
- **The test suite only ever asserted the blocking direction.** That is how an unsatisfiable gate
  shipped. Added the allow path for both runtimes, partial dismissal still blocking, the message
  naming a runnable command, and the tool's three refusals: **57 → 66 tests**.
- **Repo hygiene:** MIT `LICENSE` (there was none — default copyright on an already-public repo
  meant nobody could legally use, fork, or contribute), a GitHub Actions matrix on Python
  3.11–3.14 (green; stdlib-only, no dependency install and no ArangoDB service), README badges, and
  `.env.example` documenting the eight config keys the scripts actually resolve.
- **`argos` bootstrapped** as the 31st repo and has already produced its first `/prd-sync`
  (17 gaps, `last_sync` 2026-08-19). It is registered but has **no read yet**, which is expected
  at this age.

## 1. PRD requirement coverage

All 14 sections IMPLEMENTED. §3 (retrieval & ranking) — previously PARTIAL pending
server-side work — is now IMPLEMENTED: hybrid+graph live, superseded hard-excluded
server-side, `memory_type`/`why`/`how_to_apply` first-class on save, `memory_type` filter on
search, `co_applied` edge learning + re-embed-on-edit via skills/maintenance.

## 2. Live health (prod)

`verify.py` 2026-08-19: **ALL CHECKS PASSED**, including the MCP liveness probe — 61 tools
exposed, `developer` profile, `graph,search` toolsets, all four shared-memory tools live.

Live backlog has grown substantially since round 4: **148 open drift alerts** (207 closed, up
from 92 open), **63 unprocessed observations** (up from 31), **7 proposed PRD patches awaiting
review** (up from 1), 74 accepted. Two warnings the run surfaced:

- **1 pattern with a deferred embedding** — it is invisible to vector retrieval until
  `pattern-index` or `phase1b_setup.py` backfills it.
- **The retrieval eval is stale.** MRR 0.98 / R@5 1.00 across all three modes is unchanged — but
  it is dated **2026-08-02, n=29**, and the corpus has grown 68 → 76 patterns since. The headline
  ranking figure is now measured against a 17-day-old corpus and should be re-run before it is
  quoted again.

The review backlogs (148 alerts, 63 observations, 7 patches) are growing faster than they are
being triaged. That is a capacity signal, not a correctness one, but it compounds.

## 3. Adoption & value — the read-path (moving fast; read the caveats)

| Metric | Round 4 (08-13) | Round 5 (08-19) |
|---|---|---|
| Patterns / registered projects | 68 / 21 | **76 / 25** |
| Repos bootstrapped | 28 | **31** |
| Applied patterns (total applies) | 28/68 (86) | **29/76 (91)** |
| Surfaced by search | 63/68 | **66/76** |
| Surfaced → applied | 44% | **44%** (29/66) |
| Interactive searches (hit rate) | 71 (97%) | **80 (96%)** |
| Automatic SessionStart recalls | 116 | **127** |
| Applies per interactive search | 1.21 | **1.14** |
| Projects producing reads | 18 | **20** |
| Registered projects with no read | 4 | **5** (+`argos`, new) |

**Three caveats, and the first one is now a finding rather than a worry:**

1. **The apply signal was inflated by a defect, not merely by "pressure".** Round 4 called it
   gate-assisted. The mechanism is now known (see Round 5) and the numbers corroborate it hard:
   **91 applies, 80 recorded outcomes, 0 failures — and 0 of 76 patterns has *ever* recorded a
   failed application.** A genuine reuse population is not 100% successful. Note also that
   `applies per interactive search` fell 1.21 → 1.14 while still being >1, which remains
   implausible for clean reuse. **Every number in this table predates the 08-18 fix**, so the
   conversion figure still carries the inflation. Round 6 should be expected to show applies
   *drop*; that will be the inflation being removed, not a regression.
2. **Headcount is still one.** `search_log.by` is `arthur` for **all 80** interactive searches;
   the other 127 reads are `session_recall` (automatic, no human). Applies: `arthur` 82, unattributed 9.
   Writes: `arthur` 70, `pj` 1, unattributed 5. PJ's single contribution remains the PR #1 fix,
   with zero searches. Volume grew ~12% this round; the multi-human evidence this system exists to
   produce still does not exist, and no commit can create it.
3. **Conversion is flat.** 44% → 44% across a round in which patterns grew 12% and searches grew
   13%. Combined with caveat 1, the honest read is that this metric has not yet measured anything
   real: it moved from 29% to 44% under a defect, and has now stalled.

## 4. Remaining risks (small)

- **A metric discontinuity now exists at 2026-08-18, and it must not be read as a regression.**
  Pre-fix apply figures were produced under gate pressure; post-fix figures will not be. Any
  round-over-round comparison of `surfaced → applied` that straddles that date is comparing two
  different definitions. This is the single most important thing for the next reviewer to know.
- **Apply truth still requires agent judgment.** Hooks force one attribution pass but deliberately
  cannot infer reuse from a search alone — that remains the safe automation boundary. What changed
  is that honest disposition is now *possible*; it was not before.
- **Zero recorded failures is still unexplained.** The gate fix removes the pressure that produced
  it, but if outcomes stay 100% positive in round 6, the cause is not the gate and the funnel needs
  a harder definition. Also note only 80 of 91 applies carry an outcome at all.
- **Liveness is probed on demand, not continuously.** Unchanged from round 4 — `verify.py` catches
  a dead or under-profiled MCP server, but only when someone runs it. Every outage so far was found
  by a human noticing an absence.
- **The retrieval eval has not been re-run in 17 days** while the corpus grew 12% (§2).
- **Triage backlogs are outgrowing triage capacity:** 148 open alerts, 63 unprocessed observations,
  7 patches awaiting review.
- Registry entries without a matching current checkout still need cleanup before they can produce
  project-local reads.
- Capture miner correction-detection is still regex-narrow (by design).

## 5. Recommended next actions

**Ranked for round 6:**

1. **Get a second human reading and applying.** Unchanged as #1 from round 5, and now the *only*
   remaining item of its kind: single-operator concentration is the one weakness that no commit
   can address. `search_log.by` is `arthur` for all 80 interactive searches.
2. **Re-measure the funnel and expect it to fall.** This is the first post-fix measurement and the
   whole point of round 6. Record the 08-18 discontinuity next to the number. If applies drop and
   failures appear, the fix worked. If outcomes stay 100% positive, the gate was not the cause and
   the metric needs a harder definition than "an agent said so".
3. ~~**Make liveness continuous.**~~ **DONE 2026-08-21**, by a cheaper route than the
   scheduled probe this item originally proposed. Root cause of that day's outage: `:8529` went
   unreachable, and because the MCP server connects to ArangoDB *before* it begins serving — with
   5 retries and 1+2+4+8+16s of backoff — it never answered `initialize`, so the client reported a
   bare 30s timeout and dropped all 61 tools. Two fixes, both verified against a blackholed
   address (RFC-5737 `192.0.2.1`): **(a)** `session_recall.py` still fails open but no longer fails
   *silent* — it prints that recall is inactive, making every session start a free liveness probe
   with no scheduler and no added latency; **(b)** the server bounds its startup connect
   (`STARTUP_CONNECT_BUDGET`, default 8s) and proceeds to serve regardless, so a database outage
   degrades individual calls instead of removing the toolset. Before: no response in 30s, 0 tools.
   After: `initialize` in 8.5s, 61 tools listed, per-call failure naming the endpoint.
   **Still open:** a *scheduled* probe. The SessionStart warning only fires when someone starts a
   session, so an outage during idle hours is still discovered by a human.
4. **Rebuild the retrieval eval — do not just re-run it.** *(Revised 2026-08-19 after §6; the
   original wording, "re-run the eval, cheap, and the figure most likely to be quoted externally",
   was wrong.)* The set is **saturated**: MRR 0.98 / R@5 1.00, identical across bm25, hybrid, *and*
   hybrid+graph. A benchmark that cannot separate keyword search from the full hybrid+graph pipeline
   carries no information, so re-running it against a larger corpus produces the same number and
   teaches nothing. It also means **there is currently no evidence that the graph layer earns its
   place** — the thing most often described as the differentiator is the thing the eval is least
   able to see. Needs harder queries, per-mode deltas allowed to diverge, and an isolation run that
   attributes any gain to a specific component.
5. **Backfill the 1 deferred embedding** — it is invisible to vector retrieval until then.
6. **Triage the growing backlogs** (148 alerts / 63 observations / 7 patches), or explicitly accept
   them as a known steady state rather than letting them accumulate silently.
7. **Close the zero-read registry projects** (or retire stale entries). `argos` is newly registered
   and needs no action yet.

**Superseded from round 5:** action #3 ("validate the apply signal") is **done** — it was not
merely validated but root-caused and fixed. Its successor is #2 above.

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

**Next measurement:** run `scripts/verify.py` weekly and compare interactive searches, automatic
recalls, surfaced→applied conversion, and zero-read projects — **but treat 2026-08-18 as a
definition boundary for the apply funnel** (see §4). The first post-fix reading is the one that
matters; a decline in applies across that boundary is the expected, healthy outcome. Scheduling
mechanism is left to the operator (maintenance launchd/cron or Cursor Automation); no schedule was
silently installed.

## 6. Improvement opportunities found by external comparison (2026-08-19)

Source: a technical comparison against `~/code/arango-agentic-memory` (Michael Fonseca) — a
general-purpose ArangoDB-backed agent-memory service, 234 commits, 18.2k lines of Python, 442 tests
against a real database. It is not a competitor and not a merge candidate (different domain,
near-zero schema overlap), but it is *ahead of this project on the retrieval-engineering axis*, and
reading it surfaced five defects here that internal review had not.

**This is a method note as much as a finding.** Four rounds of self-review produced no suspicion
that the eval was saturated. One afternoon of reading a neighbouring implementation did. Comparison
against an adjacent system is a cheaper source of findings than another round of introspection, and
should be repeated rather than treated as a one-off.

**Ranked by value ÷ cost:**

1. **The eval cannot discriminate, and nothing else here is more misleading.** See revised action
   #4 above. This is the highest-value item on the list: the ranking figure is the one most likely
   to be quoted externally, and it currently supports none of the claims made near it. The
   comparison project's method is the template — a public benchmark (LongMemEval-S), a headline
   delta, and then an **isolation run** attributing +0.089 of a +0.111 gain to the entity graph
   specifically.
2. **RRF `k=10` with equal arm weights is probably wrong.** `pattern_memory_tools.py` (and its
   hand-copied twin in `eval_retrieval.py`) uses `1.0/(10+rank+1)` and weights the BM25 and vector
   arms equally. The comparison uses `k=60` — the value from the original RRF paper — and weights
   arms explicitly, on an argument worth adopting verbatim: RRF assumes all input lists rank by the
   same notion of relevance, and these do not. BM25 ranks "does this text answer the query"; vector
   ranks topical proximity to the query. Fusing them as peers over-trusts the weaker signal.
   Cheapest real improvement available, and it must land in both copies (the drift test enforces
   that).
3. **Nothing in CI touches a database.** The 66 tests are stdlib-only and hermetic, which is right
   for the hook layer that runs on teammate machines — but it means every AQL string, index, view
   and migration is unverified in CI. The outage history is unambiguous about where the bugs live:
   an AQL reserved word used as a field name took the SessionStart digest down for six days. The
   comparison runs `pytest` against a real ArangoDB via `testcontainers`, plus `ruff` and
   `mypy --strict`. A second, DB-backed CI job would not compromise the stdlib guarantee.
4. **The ranking engine has no module boundary.** It exists only inside the MCP server's tool
   handlers, so `eval_retrieval.py` copies its AQL by hand and a drift test guards the copy. That
   workaround is the symptom; the missing plain-Python module is the defect. Worth doing on its own
   merits — it is also the precondition for measuring or reusing the ranking anywhere else.
5. **No diversity control or token budget on retrieval.** Results are top-8 by score. The
   comparison applies MMR for diversity and a tiered token budget. This matters most exactly where
   this project already injects unrequested context — the SessionStart digest, which competes for
   the same window as the user's actual task.
6. **`superseded` is a boolean where it could be bi-temporal.** The flag records *that* something
   was replaced, not *when it was true*, so "what did this project believe in July" is
   unanswerable. The comparison uses bi-temporal `Supersedes` edges with write-time conflict
   detection, making contradiction a first-class event rather than a silent overwrite.
7. **Namespace collision, cheap to fix only while it is still cheap.** Both projects create a named
   graph `memory_graph`, a `relates_to` edge collection, and a vector index on an `embedding`
   field. Co-deploying them in one database today would have them fighting over object names.

**What the comparison did *not* find:** no reason to merge, and no missing capability on the
enforcement side. The hook/gate layer, evidence verification, and reuse attribution have no
counterpart in the comparison project — and the outcome-weighted ranking term
(`succ = applied_worked / (applied_worked + applied_failed)`) is a signal it lacks entirely. The
gap is concentrated in retrieval engineering and test infrastructure, not in the ideas.
