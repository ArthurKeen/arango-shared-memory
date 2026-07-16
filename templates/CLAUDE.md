# PROJECT: <PROJECT_NAME>

## Identity
- PROJECT_ID: <unique-kebab-case-id>  (e.g. "my-api", "frontend-v2")
- PROJECT_TYPE: <web-api|frontend-react|cli-tool|microservice|mobile|full-stack>
- PRD_FILE: <relative path to your PRD, e.g. docs/PRD.md>
- TECH_STACK: <e.g. "TypeScript, Node.js, PostgreSQL">

## Dark factory operating mode
This project uses autonomous drift detection. Three skills are registered:
- `/prd-sync` — audit implementation against PRD requirements
- `/pattern-save` — capture a solved problem to shared memory
- `/pattern-search <problem>` — search shared memory before solving a problem

**Mandatory protocol:**
1. Before solving any non-trivial problem: run `/pattern-search <description>` first.
2. After fixing a drift gap or discovering a reusable technique: run `/pattern-save`.
3. At the end of any session that touched implementation files: run `/prd-sync`.

## PRD location
The PRD is at `<PRD_FILE>`. It is the source of truth for what this system must do.
All implementation must be traceable to a requirement in the PRD.
If a requirement is missing from the PRD but exists in code, add it to the PRD.

## Drift policy
- A MISSING requirement is a bug, not a TODO.
- A TEST-ONLY requirement (tested but not implemented) is deceptive — fix it.
- A PARTIAL requirement must be tracked in drift_alerts until closed.
- Never mark a requirement IMPLEMENTED without a file:line reference.

## Shared ArangoDB memory
MCP server: arangodb-memory-mcp
Collections:
- shared_patterns: cross-project solutions (read via /pattern-search, write via /pattern-save)
- project_registry: this project's state and contribution count
- drift_alerts: open drift gaps for this project

## Session end checklist
Before ending any session:
- [ ] Run /prd-sync if any implementation files were modified
- [ ] Run /pattern-save for any technique worth sharing
- [ ] Check .prd-drift-queue for queued change alerts
