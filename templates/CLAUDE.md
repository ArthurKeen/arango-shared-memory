# PROJECT: <PROJECT_NAME>

## Identity
- PROJECT_ID: <unique-kebab-case-id>  (e.g. "my-api", "frontend-v2")
- PROJECT_TYPE: <web-api|frontend-react|cli-tool|microservice|mobile|full-stack>
- PRD_FILE: <relative path to your PRD, e.g. docs/PRD.md>
- TECH_STACK: <e.g. "TypeScript, Node.js, PostgreSQL">

## Dark factory operating mode
This project uses autonomous drift detection with enforcement. Registered automation:
- `/prd-sync` — audit implementation against PRD requirements (both directions: code
  gaps become drift alerts; legitimate code-ahead-of-PRD divergence becomes a proposed
  PRD patch for review)
- `/pattern-save` — capture a solved problem (or feedback/user/project/reference memory)
  to shared memory
- `/pattern-search <problem>` — search shared memory before solving a problem
- **SessionStart hook** — injects a shared-memory digest automatically (open gaps, PRD
  staleness, project feedback memories, top relevant patterns)
- **PostToolUse hook** — queues a drift marker on every implementation-file edit, and a
  distinct `prd_` marker when the PRD itself is edited
- **Stop gate** — session end is BLOCKED (once) while the drift queue is non-empty; run
  `/prd-sync` to clear it. Bypass only via a `.no-drift-gate` file (discouraged).

**Mandatory protocol:**
1. Before solving any non-trivial problem: run `/pattern-search <description>` first. Use the
   `memory_type` filter (`feedback` for standing guidance, `pattern` for reusable solutions) when
   the intent is known. If you apply any surfaced pattern, immediately record it with
   `pattern-applied` (the reuse signal is how good patterns rise and their authors get credit —
   do not defer it to session end). Most hits of an 8-result search are *not* reused; record that
   review with `python3 .claude/hooks/dismiss_surfaced.py <session_id> <key> ...`, which is local
   audit state only. Never mark a result applied just to clear the gate — that inflates
   `usage_count` and corrupts success-rate ranking for everyone else.
2. After fixing a drift gap or discovering a reusable technique: run `/pattern-save`.
3. When the user corrects you or confirms an approach worked: save it as a `feedback`
   memory (with why + how_to_apply) so the guidance persists across sessions.
4. At the end of any session that touched implementation files: run `/prd-sync`.

## PRD location
The PRD is at `<PRD_FILE>`. It is the source of truth for what this system must do.
All implementation must be traceable to a requirement in the PRD.
If a requirement is missing from the PRD but exists in code, propose it via `/prd-sync`
(it becomes a `new-requirement` PRD patch for review) — do not edit the PRD silently.

## Drift policy
- A MISSING requirement is a bug, not a TODO.
- A TEST-ONLY requirement (tested but not implemented) is deceptive — fix it.
- A PARTIAL requirement must be tracked in drift_alerts until closed.
- Never mark a requirement IMPLEMENTED without a file:line reference, and evidence must
  pass `check_evidence.py` (the mechanical gate) before it is persisted.
- Legitimate divergence (obsolete/imprecise PRD text) is OUTDATED-PRD: it produces a
  proposed PRD patch, never a silent absorption.

## Automation policy (anti-runaway)
- PRD patches are NEVER auto-applied — user acceptance is required, always.
- Evidence-verification failures cannot be overridden or forced to IMPLEMENTED.
- Unattended sessions take recorded defaults (leave proposed, record an observation,
  continue) instead of stalling — and never apply changes while unattended.

## Shared ArangoDB memory
MCP server: arangodb-memory-mcp
Collections:
- shared_patterns: cross-project memories — memory_type: pattern | feedback | user |
  project | reference (read via /pattern-search, write via /pattern-save)
- project_registry: this project's state, contribution count, PRD content hash
- drift_alerts: open drift gaps for this project
- prd_patches: proposed/accepted/rejected PRD patches (reverse drift)
- sync_observations: audit findings kept as hints for the next /prd-sync

## Session end checklist
Before ending any session:
- [ ] Run /prd-sync if any implementation files (or the PRD) were modified — the Stop
      gate enforces this while the drift queue is non-empty
- [ ] Run /pattern-save for any technique worth sharing
- [ ] Save feedback memories for any correction/confirmation the user gave
