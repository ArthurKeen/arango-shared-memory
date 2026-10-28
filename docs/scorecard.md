# System Scorecard — arango-shared-memory

_Last updated: 2026-10-21 (round 8: **round 7's five actions, carried out** — eval
rebuilt and discriminating, two measurement fixes written but not yet deployed, the
backlog diagnosed as dormancy, and the adoption question answered from the search log
rather than asked. Earlier headers preserved below.)_

_Round 7 header (2026-09-28): (round 7: **the premise finally has evidence — four humans now
read this memory**, ending the single-operator caveat that headlined every round since 3.
An external contributor found three real defects this project's own guards missed. Two
long-standing weaknesses are unchanged and one new self-inflicted gap opened: see
"Round 7". Earlier headers preserved below.)_

_Round 6 header (2026-08-28): (round 6: **the prediction held** — with the gate fixed and the
Phase-4 skill delivered, applies flattened (+5 vs +21 new patterns), conversion fell
44% → 40%, and applies-per-search converged 1.21 → 1.14 → 1.01. That decline is the
inflation leaving, not a regression — it was written down in advance for exactly this
reading. Still zero recorded failures (85/85 "worked"), so the outcome flag itself is the
next soft spot. See "Round 6" under Changes applied. Earlier header preserved below._

_Round 5 header (2026-08-25, unchanged): (round 5 + addenda: see §2, §3 caveat 1, §4 and §6.8 — a
delivery-path defect meant three skill fixes never reached 28-31 projects, which bears directly
on the funnel figure. Original round-5 body below unchanged.
Round 5: the apply gate's inflation mechanism was found and removed — it was
mathematically unsatisfiable, so the only way to clear it was the dishonesty its own
message forbade. Engineering quality improved materially: CI, a license, and the test gap that
hid the defect. No post-fix telemetry exists yet). Method: static review of `templates/`,
`scripts/`, `docs/PRD.md`, tests, the `arango-solutions-mcp-server` memory tools, plus live
`verify.py` (all-green, incl. MCP liveness) and direct read-only AQL against the shared prod
cluster (`prod.demo.pilot.arango.ai`, db `memory`)._

> **A note on identities.** This repository is public. Teammates other than the owner are
> referred to only as `user-2`, `user-3`, `user-4`, `user-5` throughout — consistently, so
> per-person figures stay comparable across rounds. The real `search_log.by` / `saved_by`
> values live in the database, where access is already controlled; they do not belong in a
> published document. Keep it that way when writing the next round: adoption claims need
> *counts per distinct person*, never the people.

## Overall (round 8, 2026-10-21): **A− / the measurement gaps are fixed in source and unproven in production**

Round 7's five actions were carried out. The retrieval eval was rebuilt and now
discriminates — hybrid beats bm25 (MRR 0.927 vs 0.894, and 0.750 vs 0.617 on the hardest
tier), while **hybrid+graph changed no result on any of 64 queries**, the first real
evidence either way about the graph layer. The two headline defects — 0 failures in 163
applies, and 1 of 402 drift alerts carrying a branch stamp — are fixed in source and
still read unchanged in the data, because neither change is deployed yet; that is the
prediction to check next round. The review backlog was diagnosed rather than drained:
it is **dormant projects, not neglect** — actively-worked projects carry zero stale
alerts. And the adoption question was answered from the search log: all three non-owner
users work exclusively in `contextual-data-fabric`, the one project deliberately
excluded from the hook rollout, with 0 empty results and a relevant top hit on every
search between them. Retrieval is not their problem; the habit-forming layer is switched
off where they work. Full detail in "Round 8".

---

### Round 7 overall (2026-09-28): A− / the premise now has evidence; measurement is the weak half

Four humans read this memory (`arthur` 103 searches, `user-2` 7, `user-3` 2, `user-4` 1), and `user-2`
both writes and reuses independently — retiring the single-operator caveat that headlined rounds
3 through 6. Volume: **406 automatic recalls, 113 interactive searches, 113 applies, 119 patterns**;
conversion steady at 40% and applies-per-search at 1.00. An external contributor found three real
defects the project's own guards missed, one of which left the stop gate's block decision
unparseable. Against that: the outcome flag still records **0 failures in 113 applies**, the
retrieval eval is saturated *and* eight weeks stale against a corpus 75% larger, the review
backlogs are growing (patches 26, observations 88, alerts 172), and my own provenance-stamp fix
reached `prd_patches` and `sync_observations` but **not `drift_alerts` (0/16)**, because
`save_drift_alert` has no `branch`/`commit` parameters. Full analysis in "Round 7" below.

---

### Round 6 overall (2026-08-28): A− / healthy and better-engineered; the value metric is now known to have been inflated

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
| Adoption & value (read-path) | **B** | = | **round 8: the ceiling has a name.** All three non-owner users work *exclusively* in `contextual-data-fabric` — the one project deliberately excluded from the hook rollout, so they get no SessionStart recall, no apply gate and no nudges. Between them: 15 searches, **0 empty results, a relevant top hit every time**. Retrieval is not what is limiting them. `user-4` did return (2026-10-19), correcting round 7. Volume: recalls 406→**545**, patterns 119→**141**, searches 113→**158**, applies 113→**163**. The C2 follow-up PR at CDF is the highest-leverage adoption action open |
| _round 7 (2026-09-28)_ | B | ↑ | **the single-operator caveat is retired.** Four humans have now searched (`arthur` 103, `user-2` 7, `user-3` 2, `user-4` 1); `user-2` has 7 saved patterns and 10 applies. Recalls 219→**406**, patterns 97→**119**, searches 95→**113**. Conversion flat at **40%** for a third consecutive reading. Not an A: 91% of searches are still one person, and two of the four appeared once and have not returned |
| **Metric integrity** | **B−** | = | **round 8: one of the two blockers is cleared, the other is written but undeployed.** The retrieval eval was rebuilt (64 queries, three hard tiers) and now **discriminates**: bm25 MRR 0.894 vs hybrid 0.927, and 0.617 vs 0.750 on lexical traps — so hybrid's value is measured rather than asserted for the first time. It also showed **hybrid+graph changing no result on any query**, which is an unflattering but real answer. Still **0 failures in 163 applies** (0 of 141 patterns ever) and **1 of 402** alerts branch-stamped: both fixed in source this round, neither deployed. Grade holds until the data moves |
| _round 7 (2026-09-28)_ | B− | = | conversion has now read 40% three times running and applies-per-search sits at **1.00**, which is what honest attribution looks like. Held back by two unresolved items, both older than this round: **0 recorded failures in 113 applies** (0 of 119 patterns has *ever* failed), and a retrieval eval that is both saturated and now 8 weeks stale |
| Engineering quality | A | = | **round 8:** 111 DB-free tests (was 88); new guards on the apply gate's three-door message (both runtimes) and on the golden eval set's shape, so tier 2 cannot decay back into a saturated tier 1. Server side: 493 tests, with three on branch provenance and two on the closed outcome value set. `scripts/triage.py` added |
| _round 7 (2026-09-28)_ | A | = | 88 DB-free tests, CI green on 3.11–3.14; the delivery-path defect class is now guarded by `test_installer_completeness.py` (references, imports, and both installers). Tempered by the fact that an outside contributor, not the guards, found the three defects that mattered this round |
| Reliability / robustness | **A−** | = | **round 8:** no new reliability defect found. The CDF exclusion is now understood to carry an adoption cost, not just a coverage one (see Adoption). Review backlog diagnosed via `triage.py`: 200 open alerts, but **2 contradicted and 0 orphaned** — the data is coherent; the volume is dormant projects, not corruption |
| _round 7 (2026-09-28)_ | A− | ↓ | the stop gate's block decision was **unparseable** whenever a shell edit triggered the reconciler (notice on stdout ahead of the JSON) — fixed, but it means the gate silently failed exactly when it had caught something. Fleet re-verified 31/31 by checksum. `contextual-data-fabric` remains deliberately excluded |
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

### Round 6 (2026-08-28): the prediction held — inflation left the funnel

The first measurement taken with both funnel defects fixed (the unsatisfiable gate,
2026-08-18; the missing Phase-4 attribution step, delivered fleet-wide 2026-08-25).
Round 5 wrote the expected outcome down in advance so a decline could not be misread as
regression. It declined:

| Metric | Round 5 (08-19) | Round 6 (08-28) | Reading |
|---|---|---|---|
| Patterns / registered projects | 76 / 25 | **97 / 28** | corpus +28% in 9 days |
| Automatic recalls | 127 | **219** (+72%) | the injected read path is the system's busiest organ |
| Interactive searches (hit rate) | 80 (96%) | **95 (97%)** | steady |
| Apply events | 91 | **96** | **flat while patterns grew 28% — the inflation stopped** |
| Surfaced → applied | 44% | **40%** | the predicted honest drop |
| Applies per interactive search | 1.14 | **1.01** | 1.21 → 1.14 → 1.01: converging on plausible |
| Recorded failures | 0 / 80 | **0 / 85** | still implausible — see below |
| Humans reading | 1 | **1** | all 95 searches `arthur`; `user-2` now 2 saved patterns |

**Interpretation.** Applies-per-search at 1.01 and a falling conversion are what honest
attribution looks like after removing a gate that manufactured it. The funnel number is
now trustworthy *as a rate of recorded applies*; what remains assertion-grade is the
outcome flag itself — **96 applies, 85 recorded outcomes, still zero failures**. Round 5
said it plainly: if outcomes stay 100% positive after the gate fix, the flag needs a
harder definition (e.g. an apply event that references the commit/test that validated
it). That is now the top metric-integrity action.

**Also landed between rounds** (each detailed in §6.8, the bug report, or commit history):
- **MCP availability hardening (08-21/22):** a transient `:8529` outage exposed that the
  server's startup DB connect gated the whole MCP protocol (31s of retry backoff vs the
  client's 30s handshake ceiling → all 61 tools dropped). Fixed: bounded, non-fatal
  startup (`STARTUP_CONNECT_BUDGET`); SessionStart hook now fails open but never silent;
  stale-client-process diagnosis documented. Liveness B− stands.
- **Skill delivery drift closed (08-25):** rollout tool now syncs skills and discovers
  half-bootstrapped projects; 28 projects were running stale protocol files (§6.8).
  Fleet: 31 repos in sync + `contextual-data-fabric` deliberately restored to
  pre-rollout state (public repo that tracks `.claude/`; enforcement there belongs in a
  reviewed PR — see the 08-25 remediation record).
- **Graph Visualizer resolved as a size-dependent product bug (08-26/28):** node
  attributes hydrate on small/filtered canvases and are absent (stub nodes) on large
  ones — user-verified both regimes; full evidence in
  `docs/visualizer/BUG-REPORT-node-hydration.md`, including a Save-on-stub data-loss
  hazard now covered by advisories. String-equality rule operator resolved as `=` (the
  UI's own operator, observed colouring correctly on a hydrated canvas). Status colour
  coding is now live on the shared theme for `drift_alerts` (red/amber/grey),
  `prd_patches` (proposed/accepted/rejected/superseded), and `sync_observations`
  (state), and the installer + guard tests generate the same.
- **The retrieval eval is UNCHANGED and still saturated** (0.98/0.98/0.98, corpus now
  97 vs the eval's 68). It remains the top-ranked open item in §6 and now measures a
  corpus 43% larger than the one it was built against.

### Round 7 (2026-09-28): the premise is no longer hypothetical — and one of my own fixes only half-landed

A deliberate strengths/weaknesses pass, measured against prod on 2026-09-28.

**Strengths**

- **The single-operator caveat is retired.** Every round since 3 carried the same
  disclaimer: one human was reading this memory, so "cross-project reuse" was an
  architecture, not an observation. It now has four readers — `arthur` (103 interactive
  searches, first 2026-07-14), `user-2` (7, first 2026-09-16), `user-3` (2, 2026-09-24),
  `user-4` (1, 2026-09-23) — and `user-2` has independently *written* 7 patterns and recorded
  10 applies. A second person searching, saving, and reusing is the one piece of
  evidence no amount of engineering could substitute for.
- **Volume grew without the metrics degrading.** Patterns 97→119, recalls 219→**406**
  (+85%), searches 95→113, applies 96→113. Conversion held at 40% and applies-per-search
  landed on **1.00** — the shape honest attribution is supposed to have, now read three
  times running.
- **An outside contributor is finding real defects.** `lokeshmuvva` filed two PRs against
  this repo and surfaced three genuine problems: a runtime `ImportError` dependency
  (`reconcile_drift_queue.py` → `drift_queue.py`) that the first version of my own guard
  stayed green through; one remaining orphan (`capture_candidates.py`, bootstrap-only);
  and a **stdout/JSON collision** that made the stop gate's block decision unparseable
  precisely when the reconciler had something to say. That is the project working as a
  shared artifact rather than a personal one.
- **The delivery-defect class is now guarded, and the fleet is verified.**
  `tests/test_installer_completeness.py` compares what is *referenced* (by path and by
  import) against what both installers actually ship, using tuple membership rather than
  substring matching — the weaker version produced a false negative on a real orphan.
  88 DB-free tests, CI green on 3.11–3.14, and all five hooks verified present in 31/31
  projects by checksum.
- **The branch guard works in the field.** Writes now carry `branch`/`commit` on the
  paths that support it, and real stamps are landing from real branches
  (`project-sentinel` on `main`, `agentic-graph-analytics` on `feat/byoc-packaging`).
- Live health: ALL CHECKS PASSED.

**Weaknesses**

- **F2 (provenance stamps) half-landed, on the path that most needs it — my defect.**
  Since the 2026-09-25 rollout, `prd_patches` carry branch stamps 5/5 and
  `sync_observations` 5/5, but `drift_alerts` carry them **0/16**. Root cause:
  `save_drift_alert` in the MCP server (`pattern_memory_tools.py:921`) accepts no
  `branch`/`commit` parameters, so the skill dutifully passes fields the tool discards.
  Drift alerts are the collection where "which branch was this true on?" matters most,
  and it is the one collection that cannot answer. Fix belongs in the server repo.
- **The outcome flag is still fiction.** 113 applies, 102 recorded outcomes,
  **0 failures** — and 0 of 119 patterns has *ever* recorded a failure. Unchanged across
  three rounds. Until something can be marked as having not worked, `success_rate` is a
  constant, and the salience multiplier built on it ranks nothing.
- **The retrieval eval is saturated *and* now stale.** Last run 2026-08-02 at n=29,
  scoring 0.98/0.98/0.98 against a 68-pattern corpus. The corpus is now 119 (+75%) and
  eight weeks have passed. A benchmark that cannot fail and is no longer run is not
  evidence of retrieval quality.
- **Triage backlogs are growing faster than they are worked.** Proposed PRD patches
  12→**26**, unprocessed sync observations 68→**88**, open drift alerts 152→**172**.
  Every one of these is by design never auto-applied, which is correct — but the
  human-review queue is the system's actual bottleneck and nothing is draining it.
- **The new readers are shallow.** 103 of 113 searches (91%) are still one person;
  `user-3` and `user-4` each searched once and have not returned. Four readers is a real
  change from one, but it is not yet a habit for three of the four.
- Deferred embeddings 1→2.

**Net:** the strengths are structural (multi-human use, external contribution, a guarded
delivery path) and the weaknesses are concentrated in measurement — an outcome flag that
cannot record failure, an eval that cannot fail, a backlog nobody drains, and one
provenance field I said was delivered that only reached two collections of three.

### Round 8 (2026-10-21): the five round-7 actions, worked

Round 7 ended with a ranked list of five actions. This round is what happened when
they were carried out. Three are done, one is delivered-but-awaiting-a-decision, and
one turned out to have an evidence-based answer instead of the question it was asked as.

Corpus at this reading: **141 patterns, 545 automatic recalls, 158 interactive
searches, 163 applies, 29 registered projects.**

**1. `save-drift-alert` now accepts `branch` / `commit` — DONE (awaiting deploy).**
Confirmed the exact mechanism: the tool declared no parameters for them, so the MCP
layer dropped both before the function body ran, while `prd_patches` and
`sync_observations` — written through generic `upsert-document`, which passes arbitrary
fields — kept theirs. Today's reading is **1 of 402** drift alerts carrying a branch
against **73 of 208** patches, so the asymmetry is confirmed, not inferred. Fixed on
`fix/shared-memory-provenance-and-outcome` in the server repo; a close now additionally
stamps `closed_branch` / `closed_commit`, because a close recorded on a feature branch
is provisional until that commit reaches the default branch. **Not yet visible in the
data: the server has to be deployed and the fleet re-rolled.**

**2. The outcome flag can say "no" — DONE (awaiting deploy).**
Two mechanisms were keeping it at yes, and only one of them was the one round 7 named.
The server computed `worked = outcome != "failed"`, so *every* unrecognised value —
`"success"`, `"ok"`, a typo, a stray space — recorded a positive signal, on top of the
parameter defaulting to `"worked"`. And the client side was worse: **the word `outcome`
appeared in no skill, hook, rule, gate message or protocol file anywhere.** The server
has supported `outcome='failed'` with full negative-signal handling since it shipped.
The capability was dark.

The structural cause was the gate itself. It offered **two doors** — applied (a success)
or dismissed (reviewed and never tried) — and a pattern that *was* tried and did not
help had nowhere to go, so the honest verdict left through the dismissal door and the
evidence vanished. Both runtimes' gates now name three outcomes, define failure
mechanically (checks it should have made pass still failing / reverted for another
approach / introduced a defect you then had to fix), and say plainly not to dismiss
something that was actually tried. Guard tests on both gates assert all three doors are
present and distinguishable. Today's reading is still **0 failures in 163 applies, 0 of
141 patterns ever failed** — unchanged, as expected, because nothing is deployed yet.
This is the round's prediction to check next time.

**3. The retrieval eval discriminates again — DONE, and it produced a real result.**
The old set could not fail: 29 queries from 2026-07 against a 68-pattern corpus, MRR
0.98 identical for bm25, hybrid *and* hybrid+graph, unrun for eleven weeks while the
corpus more than doubled. 35 new queries in three tiers were added (the original 29 kept
as a regression floor): `hard-paraphrase` states the symptom in a stuck developer's words
and withholds the pattern's distinctive vocabulary; `lexical-trap` sits on near-duplicate
clusters where surface similarity is high; `hard-concept` is vague and cross-project. All
49 expected keys were verified present before committing.

First run, 64 queries against a 141-pattern corpus:

| mode | R@1 | R@3 | R@5 | MRR |
|---|---|---|---|---|
| bm25 | 0.86 | 0.92 | 0.94 | **0.894** |
| hybrid | 0.88 | 0.98 | 0.98 | **0.927** |
| hybrid+graph | 0.88 | 0.98 | 0.98 | **0.927** |

MRR by hard tier — bm25 → hybrid: lexical-trap **0.617 → 0.750** (R@3 0.667 → 1.000),
hard-paraphrase 0.798 → 0.833, hard-concept 0.933 → 1.000. The legacy tiers score 1.000
for every mode, confirming saturation.

Two findings. **Hybrid demonstrably earns its cost**, most of all on lexical traps —
which is the first time this project has had evidence for that rather than an assertion.
And **hybrid+graph is identical to hybrid on every single query in every tier**: the
graph layer changed nothing. That is now measured rather than hidden, but it is *not*
conclusive — none of these queries was built to *require* a 1-hop `relates_to`
expansion, so the honest reading is "no observed benefit on a set that does not
specifically demand it". A relational tier is the obvious next addition, and it is now
the top open question about the architecture.

`tests/test_golden_queries.py` guards the set's shape so tier 2 cannot decay back into
tier 1 — including a check that no query contains its own answer key or names the
target's project, which is how a set gets easier without anyone deciding to make it so.

**4. The review queues — mechanism delivered, the capping decision is the owner's.**
`scripts/triage.py` reports all three queues per-project with age buckets, plus three
staleness signals that need no judgement. The first run reframes the problem entirely:
**the backlog is not un-worked, it is dormant.** Projects people are actively in carry
*zero* stale alerts — `project-sentinel` 13 open / 0 stale, `arango-schema-analyzer`
11/0, `arango-sparql-py` 11/0, `contextual-data-fabric` 8/0. The backlog is whole
projects that stopped — meaning the project itself has not run `/prd-sync` since before
the threshold, not merely that its alerts are old (see the correction below, which is the
whole point): `dnb-er` 14, `jlr-contextual-memory-fabric` 10,
`network-asset-management-demo` 10, `domyn-gdelt` 9, `fraud-intelligence` 6, and four
more with 1–2 each — **53 rows across 8 projects**. Of 200 open alerts, 106 predate
August 22, but only these 53 belong to a project that has also gone quiet. Separately,
only **2** alerts are contradicted (a closed row exists for the same
`project_id`+`req_id`) and **0** are orphaned, so the data itself is coherent.

> **Correction (2026-10-21, same day).** The first version of this paragraph named
> `arangodb-data-tools-rs` (36 open, 36 stale) as the lead example of a stopped project.
> That was wrong, and the way it was wrong matters more than the error. The project went
> 27 commits in June, 11 in July, 5 in August, then **39 days of total silence**, then 18
> commits in October, a `v0.1.0` tag on the 19th, and a `/prd-sync` on the 21st. 35 of its
> 36 open alerts were detected *during the quiet stretch*, so an alert-age test marked
> every one of them ancient — while they are in fact live tracked work against 130
> requirements in the most active project in the fleet. A sample taken in September would
> have looked terminal and been wrong.
>
> `triage.py`'s `ancient` test now requires the **project** to be dormant as well as the
> alert to be old, read from `project_registry.last_sync`. That took the proposed sweep
> from **97 rows to 53** and protected 45 across four still-syncing projects
> (`arangodb-data-tools-rs` 35, `arango-ontoextract` 6, `agentic-graph-analytics` 2,
> `argos` 2), which are now reported under "NOT ancient" instead of being silently
> included. `tests/test_triage.py` guards it.
>
> The general lesson, which applies well beyond this script: **dormancy is a property of
> the project, not of the timestamp on a row.** A metric that samples one moment cannot
> tell a dead project from a quiet one, and this system is full of such metrics.

`--age-out` moves ancient alerts to `status: "stale"` — deliberately *not* `closed`,
because closing asserts the gap was fixed and nobody has verified that, while stale
asserts only that nobody has looked in 60+ days, which the timestamps prove.

> **Updated 2026-10-28.** Two statements here were true when written and are no longer.
>
> **It was run.** On 2026-10-23, 53 alerts across 8 dormant projects moved to `stale`
> and the open count fell 188 → 135. (It took a schema fix first: `status` was a closed
> enum in this repo's own `setup_schema.py` and `"stale"` was refused with ERR 1620 —
> the database caught it and nothing was written.)
>
> **It is no longer reversible by one UPDATE.** The sweep makes *two* mutations: the
> alert status, and the denormalized `project_registry.open_gaps`, which only
> `/prd-sync` otherwise rewrites — and these projects were chosen precisely for not
> running it, so a missed repair would have been permanent. A full undo is the printed
> UPDATE **plus** `triage.py --repair-gaps --yes`, and the script now prints both
> together. An operator who followed the older wording would have restored 53 alerts
> and left 8 counters at zero.
>
> `--repair-gaps` is restricted to dormant projects on purpose. `open_gaps` has two
> writers with different definitions — `/prd-sync` sets it from its audit's
> MISSING+PARTIAL count, the repair recomputes it from live open alerts — and on a
> still-syncing project those legitimately differ, so "repairing" it would overwrite
> the audit's answer and the next sync would put it back.

**5. "Why don't the new readers come back" had an answer in the data.**
Round 7 proposed asking them. The search log answers it without asking, and corrects two
things round 7 asserted.

| | searches | empty results | relevant top hit | first | last |
|---|---|---|---|---|---|
| `arthur` | 143 | 4 | 138 | 2026-07-14 | 2026-10-21 |
| `user-2` | 11 | 0 | 11 | 2026-09-16 | 2026-10-09 |
| `user-3` | 2 | 0 | 2 | 2026-09-24 | 2026-09-24 |
| `user-4` | 2 | 0 | 2 | 2026-09-23 | 2026-10-19 |

First correction: **`user-4` did come back** (2026-10-19, nearly four weeks later), and
`user-2` is still active. Round 7's "two appeared once and have not returned" was true on
2026-09-28 and is no longer true.

Second, and the substantive one: **all three non-`arthur` users work exclusively in
`contextual-data-fabric`** — the single project deliberately excluded from the hook
rollout by the C2 decision of 2026-08-26, where the hooks and `.cursor/` additions were
removed and never replaced. They have no SessionStart recall, no apply gate, and no
nudges. Every one of `arthur`'s 143 searches happens in a project with the full
machinery; all 15 of theirs happen in the one project without it.

Retrieval was not the problem for them: **0 empty results between them, and a relevant
top hit on every single search.** The product works; the habit-forming layer was
switched off in the only place they use it. The C2 decision said enforcement at CDF
"should arrive as a reviewed PR in that repo" — **that PR has never been raised**, and
it is now the single highest-leverage adoption action available.

**Net for round 8:** two fixes written and tested but not yet deployed, so the two
headline defects (0 failures, 0 branch stamps on alerts) still read exactly as they did
in round 7 — the honest state is "fixed in source, unproven in production". One
benchmark rebuilt and now carrying real information, including an unflattering result
about the graph layer. One backlog diagnosed as dormancy rather than neglect. And one
adoption question answered from evidence, with a concrete owner action behind it.

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

**Caveat added 2026-08-25 on the 207 closed alerts.** Closure requires evidence that passes
`check_evidence.py`, and in 28 projects that gate was doing **whole-file** term matching rather
than line-local (§6.8) — a requirement could reach IMPLEMENTED with a `file:line` citation
pointing anywhere in the file so long as the term appeared somewhere in it. One project
(`contextual-data-fabric`) had no `check_evidence.py` at all, so its claims were never
mechanically verified. The closed population is therefore softer than the count suggests. This
repo is unaffected: it runs the template skills directly rather than a bootstrapped copy, so its
own audits always used the line-local gate.

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

   **Amended 2026-08-25 — a second, independent defect, and it sharpens the conclusion.**
   The funnel was not only distorted by the gate. In **28 of 31 projects** the deployed
   `pattern-search` skill was missing Phase 4 entirely — the MANDATORY apply-attribution step —
   so the protocol *never asked* the agent to call `pattern-applied` (§6.8). Attribution in
   those projects therefore happened almost exclusively because the Stop gate demanded it at
   session end, retrospectively, under duress. That is the condition that manufactures
   "yes, it worked" answers, and it explains **80 worked / 0 failed** better than the gate
   alone does. The two defects do not cancel: the missing instruction suppressed voluntary
   attribution, the gate then compelled it, and what survives is compliance data with no
   voluntary baseline to compare against. Round 5 concluded the number was inflated; the
   honest position now is that **44% rests on even less than that** — it was measured with
   the protocol's own attribution step absent from 90% of the fleet.
2. **Headcount is still one.** `search_log.by` is `arthur` for **all 80** interactive searches;
   the other 127 reads are `session_recall` (automatic, no human). Applies: `arthur` 82, unattributed 9.
   Writes: `arthur` 70, `user-2` 1, unattributed 5. user-2's single contribution remains the PR #1 fix,
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
- **Undelivered fixes are a standing risk, now mitigated but not eliminated.** Skills and hooks
  are synced and drift-checked; `templates/CLAUDE.md`, `.cursor/rules/workflow.mdc` and
  `.claude/settings.json` payloads are only partially covered, and anything added to `templates/`
  in future is unsynced until it is added to a rollout list. Verify delivery, not just commit.
- Capture miner correction-detection is still regex-narrow (by design).

## 5. Recommended next actions

**Ranked for round 7 (2026-09-28):**

1. **Add `branch`/`commit` to `save_drift_alert`.** The one item that is a live, known,
   half-delivered fix of mine rather than a judgement call. `prd_patches` 5/5 and
   `sync_observations` 5/5 carry stamps; `drift_alerts` **0/16**, because the tool drops the
   fields the skill passes (`pattern_memory_tools.py:921`, server repo). Smallest change on this
   list and the one on the collection where branch provenance matters most.
2. **Give the outcome flag a way to say "no".** 113 applies, 0 failures, 0 of 119 patterns ever
   failed — the third consecutive round with this reading, so the gate fix was not the cause.
   `success_rate` is currently a constant and the salience multiplier built on it ranks nothing.
   Needs a definition of failure that an agent cannot satisfy by assertion.
3. **Rebuild the retrieval eval.** Now both saturated (0.98/0.98/0.98, identical across bm25,
   hybrid, and hybrid+graph) *and* stale (last run 2026-08-02, n=29, against a 68-pattern corpus
   that is now 119). Detail unchanged in item 4 of the round-6 list below.
4. **Drain, or deliberately cap, the review queues.** Proposed patches 12→26, unprocessed
   observations 68→88, open alerts 152→172. Never auto-applying these is correct policy; growing
   them without a review cadence turns the policy into a silent backlog.
5. **Deepen the new readers.** Four humans have searched, but three of them totalled 10 searches
   between them and two have not returned. The blocker is no longer "is anyone else using it" but
   "does it stick after the first try" — worth asking `user-3` and `user-4` directly what the
   first session did or did not give them.

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

**8. Fixes were not reaching the fleet at all — the delivery path, not the logic, was broken.**
*(Added 2026-08-25.)* `rollout_cursor_hooks.py` synced hooks and settings and nothing else, while
`bootstrap_project.sh` places skills once and skips existing files. Skills therefore had **no
refresh path**, and nothing compared them, so the drift was invisible rather than known-and-
deferred. Measured before the fix:

| File | Stale in | Consequence |
|---|---|---|
| `pattern-search/SKILL.md` | 28 / 31 | Phase 4 absent — the protocol never asked for `pattern-applied`; also no `memory_type` filter and no `hybrid+graph` mode documented |
| `prd-sync/check_evidence.py` | 28 / 31 | whole-file instead of line-local term matching — the evidence gate was too permissive (fixed in *round 1*, a month earlier, never delivered) |
| `prd-sync/SKILL.md` | 29 / 32 | the Phase-5 drift-queue loop, and the pre-`AGENTS.md` identity path |

Two compounding causes, both now closed: skills are synced (`CLAUDE_SKILL_FILES`), and
`discover()` keyed solely on one hook file, which silently excluded the projects most likely to be
stale — a 32nd project had 8 skills, zero hooks, and no evidence gate whatsoever, and had been
unreachable by every rollout ever run. All 32 copies now match the templates.

**The generalisable lesson is about delivery, and it is the same shape as the rest of this
scorecard's findings.** A correct fix, committed a month earlier, sat undelivered in 28 projects
while the metric it affected was analysed across two rounds. Nothing reported a problem, because
nothing was measuring whether deployed artifacts matched their source. **A fix that has not been
verified as delivered has not been made** — and for anything installed by copy, drift detection
belongs in the installer, not in someone's memory.

**What the comparison did *not* find:** no reason to merge, and no missing capability on the
enforcement side. The hook/gate layer, evidence verification, and reuse attribution have no
counterpart in the comparison project — and the outcome-weighted ranking term
(`succ = applied_worked / (applied_worked + applied_failed)`) is a signal it lacks entirely. The
gap is concentrated in retrieval engineering and test infrastructure, not in the ideas.

## 7. Addendum 2026-08-25 (evening): visualizer investigation resolved; a correction to my own record

**The Graph Visualizer "sea of alerts" thread ended at a product bug, verified by the
operator, not by this repo's tooling.** Same browser, same login, same moment: the
platform Collections UI shows `drift_alerts` documents fully populated, while the Graph
Visualizer's node Properties panel shows two-field stubs whose `_key` illegally contains
`/` — frontend-synthesized objects, not documents. All 379 alerts are intact server-side
(field census + `DECODE_REV` last-write audit). Evidence package and reproduction:
`docs/visualizer/BUG-REPORT-node-hydration.md`.

**Hazard shipped with an advisory:** the stub panel is editable with a **Save** button —
saving could overwrite a real document with the stub. Until fixed upstream, the
visualizer is read-only for documents (advisory added to ONBOARDING.md and the
visualizer-customizer skill).

**Correction of this repo's record.** Today's visualizer commits (`a7f9ef7`, `e0fcb4e`)
asserted operator mechanisms as verified — "`==` never matches", "`=` paints every node
the first rule's colour", "numeric rules verified working". None of those were verified;
canvas nodes carry no attributes, so no rule effect was ever observable, and base
collection colours were mistaken for rule effects. The skill, installer, and tests now
separate observations from mechanisms and define "verified" as *read back from
`_graphThemeStore` after UI authoring, or a colour change attributable to a rule*.

**New tracked item — `drift_alerts` schema sediment (pre-existing, organic):** ~18
long-tail fields exist on only 1–8 documents each, with near-duplicate conventions
(`last_seen`/`last_seen_at`, `state`/`status`, `requirement_id`/`req_id`,
`closed_reason`/`closing_note`/`resolution`). Cause: successive prd-sync skill versions
wrote different field sets and re-syncs REPLACE alert documents, so fields written by
older versions survive only on never-resynced docs. Core fields are 379/379; old values
are unrecoverable (single-revision store). Fix belongs with the now-synced skill fleet:
define a stable alert field set in prd-sync, then a one-time normalisation pass —
planned separately, not executed as part of the 08-25 remediation.

**C2 decision (2026-08-26, owner):** `contextual-data-fabric` was FULLY RESTORED to its
pre-rollout state (hooks and `.cursor/` additions deleted; `settings.json` + 3 skills +
`.gitignore` restored — the settings backup preserved the owner's own uncommitted diff).
Rationale: unlike the other 31 projects, CDF **tracks `.claude/` in a public repo**, so
the rollout amounted to unreviewed changes to published, PR-governed content. Enforcement
there, if wanted, should arrive as a reviewed PR in that repo. Consequences accepted:
fleet is 31/32 by design, CDF sessions follow the committed (month-stale) skill text, and
`rollout_cursor_hooks.py` will list CDF as "would-update" on every dry run until an
exclusion mechanism or that PR exists.
