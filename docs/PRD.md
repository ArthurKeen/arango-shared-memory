# PRD — arango-shared-memory

Product requirements for the multi-project shared-memory and drift-detection system.
This document is the source of truth for what the system must do. Every requirement is
numbered so `/prd-sync` can audit this repository against its own PRD.

Status legend used by audits: IMPLEMENTED / PARTIAL / MISSING / TEST-ONLY / SKIP / OUTDATED-PRD.

---

## 1. Shared memory store

- **REQ-001** The system MUST store cross-project memories in a shared ArangoDB database
  (`memory` by default) reachable by every bootstrapped project through a single globally
  registered MCP server (`arangodb-memory-mcp`).
- **REQ-002** The core collections MUST be: `shared_patterns`, `project_registry`,
  `drift_alerts`, `search_log`, `prd_patches`, `sync_observations`, and
  `schema_migrations`, with persistent indexes on the query paths used by the skills
  (`shared_patterns[problem_category, project_type, created_at]`,
  `drift_alerts[project_id, status, detected_at]`, `search_log[project_id, created_at]`,
  `prd_patches[project_id, review_state, created_at]`,
  `sync_observations[project_id, state, created_at]`).
- **REQ-003** Schema setup MUST be idempotent: re-running any setup or migration script
  against an existing database must be a no-op for objects already in the desired state.
- **REQ-004** Collections MUST carry server-side JSON schema validation at level
  `moderate`, so structurally invalid new writes are rejected while pre-existing
  documents remain readable and updatable.
- **REQ-005** Memory documents MUST NOT contain credentials, secrets, or client-specific
  confidential data; skills must instruct agents to refuse such saves.

## 2. Memory taxonomy

- **REQ-010** Every memory document MUST carry a `memory_type` field with one of:
  `pattern` (a solved problem + verified solution), `feedback` (a correction or a
  confirmed approach), `user` (who a user is: role, preferences, expertise),
  `project` (in-flight project state, decisions, constraints), `reference`
  (pointer to an external system or document).
- **REQ-011** `feedback` memories MUST include `why` (the reason behind the guidance)
  and `how_to_apply` (concrete application instructions) fields.
- **REQ-012** The default `memory_type` is `pattern`; documents predating the taxonomy
  MUST be migrated to `memory_type: "pattern"`.
- **REQ-013** All memory types MUST be retrievable through the same search path and be
  eligible for the session-start recall digest (REQ-030).

## 3. Retrieval and ranking

- **REQ-020** Retrieval MUST be server-side hybrid: semantic (vector) + keyword (BM25)
  fused via RRF, re-ranked by graded salience (importance, recency decay, usage), with
  graceful degradation to BM25-only and then tag-filter-only when components are absent.
- **REQ-021** Superseded memories MUST NOT crowd out live ones: they are demoted to
  `importance: 1` on supersede, and search paths SHOULD exclude `superseded == true`
  documents entirely (the fallback query in the skill MUST exclude them).
- **REQ-022** Every applied pattern MUST be recorded (`pattern-applied`) so
  `usage_count` / `last_used` feed ranking, and the surfaced→applied funnel is measurable.
- **REQ-023** When two or more patterns are applied together, their co-application MUST
  be recorded on `pattern_relates_to` edges (`co_applied` counter) so relatedness can
  learn from real usage, not just embedding similarity.
- **REQ-024** Graph edges (`pattern_relates_to`) MUST carry a `weight` combining
  embedding similarity and observed co-application; periodic maintenance recomputes it.
- **REQ-025** When a memory's text (`problem_description` / `solution_summary`) is
  edited, its embedding MUST be refreshed: the update sets `embedding_pending: true`
  and the next embedding pass re-embeds it.

## 4. Session-start recall

- **REQ-030** Bootstrapped projects MUST inject a shared-memory digest at session start
  (SessionStart hook): open drift alerts, last sync time, top-ranked relevant memories
  for the project/type, and all `feedback` memories scoped to the project.
- **REQ-031** The recall hook MUST fail open: if ArangoDB is unreachable or credentials
  are absent, it exits silently within its timeout and the session starts normally.
- **REQ-032** The recall hook MUST detect PRD staleness: if the hash of the project's
  PRD file differs from `project_registry.prd_sha256`, the digest says the PRD changed
  since the last sync.

## 5. Drift detection — PRD → code

- **REQ-040** `/prd-sync` MUST extract numbered requirements (REQ-NNN) from the
  project's PRD and classify each as IMPLEMENTED / TEST-ONLY / PARTIAL / MISSING /
  SKIP / OUTDATED-PRD, with `file:line` evidence for any IMPLEMENTED claim.
- **REQ-041** Each MISSING/PARTIAL requirement MUST be persisted as a `drift_alerts`
  document (idempotent key `<PROJECT_ID>_<REQ_ID>`) linked to its project node; closing
  requires `closed_evidence` and arms the TTL on `closed_at`.
- **REQ-042** Editing an implementation file MUST queue a drift marker
  (`.prd-drift-queue/`) via a PostToolUse hook; editing the PRD itself MUST queue a
  distinct `prd_`-prefixed marker.
- **REQ-043** IMPLEMENTED/PARTIAL evidence MUST be mechanically verified before it is
  persisted: an evidence-checking script confirms each cited file exists and each cited
  line is in range; unverifiable IMPLEMENTED claims are downgraded to PARTIAL.
- **REQ-044** A drift audit MUST NOT be blocked by PRD ambiguity; ambiguity is recorded
  as an observation (REQ-060) and the audit continues.

## 6. Drift detection — code → PRD (reverse drift)

- **REQ-050** When the implementation legitimately diverges from the PRD (obsolete
  requirement, deliberate improvement, imprecise spec), `/prd-sync` MUST record a
  `prd_patches` document with a typed delta (`missing-semantics | wrong-signature |
  typo | obsolete | clarification | new-requirement`), the observed behavior with
  evidence, the proposed PRD text, and a justification.
- **REQ-051** PRD patches MUST move through a review state machine:
  `proposed → accepted | rejected | superseded`. A patch is applied to the PRD file
  only after explicit user acceptance; the applied patch records `applied_at`.
- **REQ-052** PRD patches MUST never be auto-applied. In an unattended session the
  default is: leave `proposed`, record an observation, continue the audit.
- **REQ-053** `project_registry` MUST track `prd_sha256` and `prd_checked_at` so both
  the recall hook (REQ-032) and `/prd-sync` can detect PRD edits since the last audit.

## 7. Observations — learning survives rejection

- **REQ-060** Findings that do not become alerts or accepted patches (rejected patches,
  ambiguities, edge cases, deprecation signals) MUST be appended to `sync_observations`
  with a typed `observation_type` and a state machine
  (`unprocessed → acknowledged → promoted | rejected | duplicate`).
- **REQ-061** `/prd-sync` MUST read the project's `unprocessed` observations at the
  start of an audit and use them as hints, marking consumed ones `acknowledged` —
  so the same discovery is never re-derived from scratch.

## 8. Enforcement

- **REQ-070** The session Stop hook MUST block session end (decision `"block"`) while
  the drift queue is non-empty, with these rails: it never blocks twice in a row
  (respects `stop_hook_active`), it fails open on any internal error, and it can be
  bypassed per-repo with a `.no-drift-gate` marker file.
- **REQ-071** All hooks MUST be self-configuring from the project's CLAUDE.md
  (PROJECT_ID, PRD_FILE) — no per-project rendering of hook code.
- **REQ-072** Cursor rules (`workflow.mdc`) MUST describe the same save/search flows as
  the Claude skills (server-side tools, embed-then-insert), never a stale alternative.

## 9. Lifecycle and scheduled maintenance

- **REQ-080** Lifecycle passes MUST exist for: near-duplicate supersede (demote, edge,
  never delete), TTL expiry of closed alerts only, and a stale-memory report that never
  deletes.
- **REQ-081** A single maintenance entry point (`scripts/maintain.py`) MUST run all
  periodic passes in order (embedding backfill, graph rebuild, edge-weight recompute,
  provenance for patches/observations, lifecycle, health check) with `--dry-run`
  support; LLM-cost passes run only with `--with-llm`.
- **REQ-082** Maintenance MUST be schedulable without human attention: an installer
  registers a periodic scheduled job (launchd on macOS; a printed cron line elsewhere)
  and can uninstall it.

## 10. Migration

- **REQ-090** A migration script (`scripts/migrate.py`) MUST bring any existing
  shared-memory database to the current schema by auto-detection: each migration
  declares a detect predicate against the live database and applies only when needed,
  independent of what the ledger says (self-healing).
- **REQ-091** Applied migrations MUST be recorded in `schema_migrations` with a
  timestamp and description; `--dry-run` lists pending migrations without applying.
- **REQ-092** Migrations MUST be non-destructive: they add collections, indexes,
  fields, edges, and validation — they never delete or rewrite user data.

## 11. Analytics and health

- **REQ-100** `verify.py` MUST check: connectivity as the configured (possibly scoped)
  user, all collections including migration-added ones, expected indexes, a
  non-polluting write→read→delete round-trip, the BM25 view, the graph layer (graph,
  edge collections, vector index, TTL index — informational when embeddings are not
  configured), and schema-validation presence.
- **REQ-101** `verify.py` MUST print the adoption snapshot and read-path scorecard:
  patterns, per-project gaps/contributions/last-sync, open vs closed alerts, patch
  counts by review state, observation counts by state, searches, hit rate, and the
  surfaced→applied conversion.

## 12. Deployment, security, provisioning

- **REQ-110** The common path is joining one shared cluster; standing up a new backend
  is an admin path. Setup scripts must not be required (or runnable without admin
  rights) for teammates joining an existing memory.
- **REQ-111** Each developer gets a least-privilege database user (`rw` on `memory`
  only, no `_system`), provisioned and revocable via `add_teammate.py`.
- **REQ-112** Bootstrap MUST git-ignore all personal infrastructure (`CLAUDE.md`,
  `.claude/`, `.prd-drift-queue/`) in target projects, and MUST install hooks/skills
  only from `templates/` (no hand-copying).

## 13. Automation policy (anti-runaway)

These bound any current or future automation that changes project artifacts or memory:

- **REQ-120** No automated write to a PRD, codebase, or memory demotion may be applied
  without either explicit user acceptance in-session or a previously accepted patch in
  `prd_patches`.
- **REQ-121** Hard gates are not overridable by convenience flags: evidence-verification
  failures (REQ-043) cannot be forced to IMPLEMENTED; supersede never deletes; TTL never
  touches open alerts.
- **REQ-122** Unattended sessions use recorded defaults instead of stalling: when a
  decision point is reached with no user available, take the documented default
  (record + proceed), and log what was decided so it is reviewable afterward.

## 14. Engineering quality

- **REQ-130** The repository MUST declare its Python dependencies (`requirements.txt`)
  and carry runnable tests for logic that does not require a live database
  (`python3 -m unittest discover tests`).
- **REQ-131** Scripts MUST keep the three-tier connection resolution (env →
  `arangodb-memory-mcp` MCP config → defaults) so one configuration serves the MCP
  server, the scripts, and the hooks.
