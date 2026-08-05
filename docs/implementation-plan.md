# Implementation plan — 2026-07 hardening & feature round

Scope: fix known defects, close the highest-leverage capability gaps, and ship an
auto-detecting migration path for existing shared-memory databases. Requirement IDs
reference [PRD.md](PRD.md).

## Workstream A — Defect fixes

| # | Defect | Fix | Files | REQ |
|---|--------|-----|-------|-----|
| A1 | Cursor rule taught the pre-vector-index flow (hand-written `CONTAINS` AQL, `upsert-document` saves) that the vector index now rejects | Rewrite to the same server-side tools as the Claude skills | `templates/.cursor/rules/workflow.mdc` | REQ-072 |
| A2 | `phase1_setup.py` printed a hardcoded "vector index currently DISABLED" readiness message, false once Phase 1b shipped | Probe the live database (vector index present? embedded docs? key resolvable?) and print actual state | `scripts/phase1_setup.py` | REQ-100 |
| A3 | `phase2b_extract.py` with 0 patterns but ≥1 alert sent an empty PATTERNS list to the LLM once per alert (wasted calls) | Restructure control flow: skip the addresses pass without patterns; still run the dependency pass when ≥2 alerts | `scripts/phase2b_extract.py` | — |
| A4 | Superseded patterns demoted but never excluded from search | Fallback AQL now filters `superseded != true`; exclusion documented as the expected server behavior | `templates/.claude/skills/pattern-search/SKILL.md` | REQ-021 |
| A5 | Editing a pattern's text left its embedding stale | Update flow sets `embedding_pending: true`; the embedding pass already re-embeds pending docs | `templates/.claude/skills/pattern-save/SKILL.md` | REQ-025 |
| A6 | `verify.py` checked no graph-layer objects and omitted `search_log` | Add graph/edge/vector-index/TTL/validation checks + new collections + patch/observation counts | `scripts/verify.py` | REQ-100, REQ-101 |
| A7 | No dependency manifest, no tests | `requirements.txt`; stdlib `unittest` suite for DB-free logic | `requirements.txt`, `tests/` | REQ-130 |

## Workstream B — Critical capability gaps

| # | Capability | Design | Files | REQ |
|---|-----------|--------|-------|-----|
| B1 | **Session-start recall** | SessionStart hook (stdlib-only Python, 10s budget, fail-open) reads PROJECT_ID/PRD_FILE from CLAUDE.md, resolves credentials env→MCP config→defaults, queries via HTTP API, prints a digest: open gaps, last sync, PRD-hash staleness, top-ranked memories, project feedback memories | `templates/.claude/hooks/session_recall.py`, `templates/.claude/settings.json` | REQ-030–032 |
| B2 | **Memory taxonomy** | `memory_type ∈ {pattern, feedback, user, project, reference}` on `shared_patterns`; feedback carries `why` + `how_to_apply`; save skill gathers type-specific fields and merges them post-save; recall digest surfaces feedback memories | `templates/.claude/skills/pattern-save/SKILL.md`, hooks, `scripts/migrate.py` | REQ-010–013 |
| B3 | **Blocking stop gate** | Stop hook emits `{"decision":"block"}` while the drift queue is non-empty; rails: `stop_hook_active` loop-breaker, fail-open, `.no-drift-gate` bypass | `templates/.claude/hooks/drift_stop_gate.sh`, `templates/.claude/settings.json` | REQ-070 |
| B4 | **Reverse drift (code → PRD)** | `prd_patches` collection + review state machine; `/prd-sync` Phase 2 adds OUTDATED-PRD class, Phase 4 persists proposed patches, Phase 6 applies only on user acceptance; PRD content hash in `project_registry`; PostToolUse hook queues `prd_` markers on PRD edits | `templates/.claude/skills/prd-sync/SKILL.md`, `templates/.claude/hooks/drift_queue.py`, `scripts/setup_schema.py` | REQ-050–053 |
| B5 | **Evidence verification** | `check_evidence.py` (stdlib): claims JSON in, per-claim verdict out (file exists, line in range, optional term match); prd-sync runs it before persisting; failed IMPLEMENTED → PARTIAL | `templates/.claude/skills/prd-sync/check_evidence.py` | REQ-043, REQ-121 |
| B6 | **Scheduled maintenance** | `maintain.py` single entry point (backfill → graph → weights → provenance → lifecycle → verify; `--with-llm` adds the LLM edge pass); `install_maintenance_schedule.sh` registers a weekly launchd job (macOS) or prints the cron line | `scripts/maintain.py`, `scripts/install_maintenance_schedule.sh` | REQ-080–082 |

## Workstream C — Useful features

| # | Feature | Design | Files | REQ |
|---|---------|--------|-------|-----|
| C1 | **Observations log** | `sync_observations` collection; prd-sync writes rejected/ambiguous/noteworthy findings; next audit consumes `unprocessed` ones as hints | `scripts/setup_schema.py`, `templates/.claude/skills/prd-sync/SKILL.md` | REQ-060–061 |
| C2 | **Usage-learned edges** | `co_applied` counter bumped by the search skill when ≥2 patterns are applied together; `weight = 0.7·sim + 0.3·log(1+co_applied)/log(11)` recomputed by maintenance | `templates/.claude/skills/pattern-search/SKILL.md`, `scripts/maintain.py` | REQ-023–024 |
| C3 | **Collection schema validation** | JSON schema, level `moderate`, on all six business collections; applied by setup for new installs and by migration for existing ones | `scripts/setup_schema.py`, `scripts/migrate.py` | REQ-004 |
| C4 | **Automation policy** | Anti-runaway rules codified in the PRD (§13) and the project template protocol (no auto-apply, hard gates, unattended defaults) | `docs/PRD.md`, `templates/CLAUDE.md` | REQ-120–122 |
| C5 | **Safe `--force` re-bootstrap** | Files a forced re-run actually changes are first backed up as `<file>.pre-update.<timestamp>` (byte-identical files skipped as `unchanged`); backups outside `.claude/` are git-ignored — the undo for local customizations in a tree with no git history | `scripts/bootstrap_project.sh` | REQ-113 |

## Workstream D — Migration for existing databases

`scripts/migrate.py`: every migration is `(id, description, detect(db) → bool, apply(db))`.
Detection runs against the live database, so re-runs self-heal even if the ledger claims
a migration was applied. Applied ids are recorded in `schema_migrations`. Non-destructive
by construction (adds only). `--dry-run` prints what would run.

Planned migrations:

1. `m001_collections` — create `prd_patches`, `sync_observations`, `schema_migrations`, `search_log` (older installs may predate it) + their indexes.
2. `m002_graph` — ensure `memory_graph` and all eight edge definitions (`pattern_relates_to`, `pattern_supersedes`, `pattern_from_project`, `alert_from_project`, `pattern_addresses_requirement`, `requirement_depends_on`, `patch_from_project`, `observation_from_project`).
3. `m003_memory_type` — backfill `memory_type: "pattern"` on typeless memories.
4. `m004_edge_weights` — backfill `co_applied: 0` and `weight: sim` on `pattern_relates_to`.
5. `m005_validation` — attach JSON schema validation (moderate) to the business collections.

## Server-side changes — SHIPPED (separate MCP-server repository)

These once-deferred server-side items are now implemented in `arango-solutions-mcp-server`
(`mcp_tools/pattern_memory_tools.py`), so the client skills no longer need workarounds:

- `pattern-search` **hard-excludes `superseded == true`** in all three ranking AQLs
  (hybrid, hybrid+graph, BM25); the `as_of` time-travel view intentionally re-includes
  what was valid at that instant.
- `save-pattern` accepts **`memory_type` / `why` / `how_to_apply` as first-class inputs**
  and stamps them server-side (the save skill no longer does a post-save merge).
- `pattern-search` now takes an optional **`memory_type` filter parameter** (validated;
  ANDed onto the validity filter in every mode).

## Out of scope here (tracked, not silently dropped)

- **Automated remediation of drift gaps** — deliberately excluded; §13 of the PRD defines
  the gates any future version must satisfy.
- **Windows scheduler support** for maintenance (launchd + cron covered).
- **Automatic `pattern-applied` capture** — reuse is still recorded by the agent per the
  skill protocol (now MANDATORY in CLAUDE.md); a server/hook-driven capture is a candidate
  if the surfaced→applied metric stays low.

## Verification

1. `python3 -m py_compile` on every changed/added script; `bash -n` on shell scripts; JSON parse on settings.
2. `python3 -m unittest discover tests` (DB-free tests).
3. Against a live database: `migrate.py --dry-run` → `migrate.py` → `verify.py` (expects new checks green), then `maintain.py --dry-run`.
4. Bootstrap a scratch project and confirm: session digest appears at start, editing a source file queues a marker, editing the PRD queues a `prd_` marker, Stop is blocked once until `/prd-sync` clears the queue, `.no-drift-gate` bypasses.
