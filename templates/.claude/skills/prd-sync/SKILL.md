# PRD Sync Skill

## Invocation
`/prd-sync` — audit implementation files against the PRD and write drift gaps to ArangoDB.

## Purpose
Find gaps between what the PRD says the system must do and what is actually implemented —
in BOTH directions: requirements the code doesn't satisfy (drift alerts), and PRD text the
code has legitimately outgrown (PRD patches, applied only with user approval).
Every requirement must have a `file:line` evidence reference or be classified as MISSING/PARTIAL.

---

## Protocol

### Phase 0 — Locate the PRD + staleness check
Read `PRD_FILE` from CLAUDE.md. If not found, search for files matching `*PRD*`, `*requirements*`, `*spec*` in `docs/`. If still not found, ask the user.

Compute the PRD content hash and compare it to the stored one:
```bash
shasum -a 256 <PRD_FILE> | cut -d' ' -f1
```
```
Use tool: execute-aql-query   database_name: "memory"
query: RETURN DOCUMENT("project_registry", @pid).prd_sha256
bind_vars: { "pid": "<PROJECT_ID>" }
```
If they differ (or no hash is stored), note in the report: **"PRD changed since last sync"** —
requirement numbering may have shifted, so re-extract everything rather than assuming prior REQ ids.

### Phase 1 — Extract requirements (+ consume prior observations)
First pull this project's unprocessed observations — discoveries from earlier audits that were
recorded but not yet acted on. Use them as hints (ambiguities already found, edge cases already
hit) instead of re-deriving them:
```
Use tool: execute-aql-query   database_name: "memory"
query: FOR o IN sync_observations
         FILTER o.project_id == @pid AND o.state == "unprocessed"
         SORT o.created_at RETURN o
bind_vars: { "pid": "<PROJECT_ID>" }
```
Mark each observation you actually use as acknowledged (`UPDATE o WITH { state: "acknowledged" }`).
Skip silently if the collection doesn't exist (backend not yet migrated).

Then parse the PRD and extract every distinct, testable requirement. A requirement is any statement that describes what the system MUST, SHOULD, or SHALL do.

Number them: `REQ-001`, `REQ-002`, etc.

Output a table:
```
REQ-001 | The system must authenticate users via JWT | PENDING
REQ-002 | The API must return 400 for missing fields | PENDING
...
```

### Phase 2 — Audit implementation

For each requirement, search the implementation (src/, lib/, app/, api/ — wherever code lives):

```bash
grep -rn "<key term from requirement>" src/ lib/ app/ api/ 2>/dev/null | head -20
```

Classify each requirement:
- **IMPLEMENTED** — found in implementation code with `file:line` evidence
- **TEST-ONLY** — found only in test files (`*.test.*`, `*.spec.*`, `*_test.*`)
- **PARTIAL** — some parts implemented, others missing
- **MISSING** — no evidence found anywhere
- **SKIP** — infrastructure/deployment requirement, not verifiable in code
- **OUTDATED-PRD** — the code deliberately and legitimately diverges: the requirement is
  obsolete, imprecise, or the implementation is a documented improvement. This is drift in the
  PRD, not the code — it produces a PRD *patch* (Phase 4b), never a drift alert. Use sparingly
  and only with evidence; "we didn't get to it" is MISSING, not OUTDATED-PRD.

**Never mark IMPLEMENTED without a file:line reference.**

### Phase 2.5 — Verify evidence mechanically (the confabulation gate)

Before writing the report, verify every `file:line` citation with the checker script — a claim
that cannot be mechanically confirmed must not be persisted as IMPLEMENTED:

```bash
python3 .claude/skills/prd-sync/check_evidence.py <<'EOF'
{"claims": [
  {"req_id": "REQ-001", "classification": "IMPLEMENTED",
   "evidence": ["src/auth/jwt.ts:42"], "term": "jwt"},
  {"req_id": "REQ-007", "classification": "PARTIAL",
   "evidence": ["src/api/users.ts:89"]}
]}
EOF
```

It verifies each cited file exists, each cited line is in range, and (when `term` is given) the
term appears near the cited line. **Any IMPLEMENTED claim with a failed verdict is downgraded to
PARTIAL** with gap `evidence unverifiable: <reason>`. Exit code 1 means at least one claim failed
— fix the classifications before Phase 3. This gate is not overridable.

### Phase 3 — Drift report

Emit a structured report:

```
[PRD-SYNC] Drift Report — <project> — <date>

SUMMARY: X implemented | Y partial | Z missing | W test-only | V skip

IMPLEMENTED (X):
  REQ-001 src/auth/jwt.ts:42 — JWT validation middleware
  ...

PARTIAL (Y):
  REQ-007 src/api/users.ts:89 — POST /users exists but missing input validation
  Gap: field validation not present

MISSING (Z):
  REQ-012 — Rate limiting on all endpoints
  REQ-015 — Audit log for admin actions

TEST-ONLY (W):
  REQ-009 tests/auth.test.ts:33 — "should reject expired tokens" (test exists, impl missing)

OUTDATED-PRD (U):
  REQ-018 src/queue/redis.ts:12 — PRD mandates RabbitMQ; implementation moved to Redis Streams
  Proposed patch: <one-line summary; full patch persisted in Phase 4b>
```

### Phase 4 — Write to ArangoDB (skip if MCP unavailable)

For each MISSING or PARTIAL requirement, write a drift alert with **`save-drift-alert`**
(NOT a raw `upsert-document` into `drift_alerts`). This tool upserts the alert AND
links it to its project node via an `alert_from_project` edge, so drift alerts and
their projects never become orphan nodes in the memory graph:

```
Use tool: save-drift-alert
project_id: "<PROJECT_ID>"
req_id: "<REQ-NNN>"
requirement: "<requirement text>"
classification: "MISSING" | "PARTIAL"
status: "open"
evidence: "<file:line or empty>"
gap_description: "<what is missing>"
detected_at: "<ISO timestamp>"
```

It is idempotent on `<PROJECT_ID>_<REQ_ID>`: identity (`project_id`/`req_id`) is
preserved and only the fields you pass are merged on re-detection, so re-running
`/prd-sync` keeps each alert's identity and its provenance edge.

For each IMPLEMENTED requirement where a previous alert was open, close it with the
same tool (pass `status: "closed"` and the closing evidence):

```
Use tool: save-drift-alert
project_id: "<PROJECT_ID>"
req_id: "<REQ-NNN>"
status: "closed"
closed_at: "<ISO timestamp>"
closed_evidence: "<file:line>"
```

> If your MCP server predates `save-drift-alert`, reload it; as a last resort you
> can still `upsert-document` into `drift_alerts`, but that leaves the alert an
> orphan until `phase2_setup.py` next runs.

### Phase 4b — Persist PRD patches (reverse drift)

For each OUTDATED-PRD finding, write a **proposed** patch (never applied here — Phase 6):

```
Use tool: upsert-document
collection_name: "prd_patches"
search_fields: { "_key": "<PROJECT_ID>_<REQ_ID>_<YYYYMMDD>" }
document_data: {
  "_key": "<PROJECT_ID>_<REQ_ID>_<YYYYMMDD>",
  "project_id": "<PROJECT_ID>",
  "req_id": "<REQ-NNN>",
  "delta_type": "missing-semantics" | "wrong-signature" | "typo" | "obsolete" | "clarification" | "new-requirement",
  "observed": "<what the code actually does, with file:line>",
  "proposed_patch": "<the exact replacement/additional PRD text>",
  "justification": "<why the PRD, not the code, should change>",
  "review_state": "proposed",
  "created_at": "<ISO timestamp>"
}
update_data: { "observed": "<...>", "proposed_patch": "<...>", "justification": "<...>" }
```
Re-detection merges into the same key; a patch already `accepted`/`rejected`/`superseded` is
never flipped back to `proposed` — create a new dated key if the situation genuinely changed.

### Phase 4c — Persist observations (learning survives rejection)

Findings that become neither an alert nor a patch still carry information: PRD ambiguities,
edge cases discovered while grepping, deprecation signals, and any patch the user rejects in
Phase 6. Append each to `sync_observations` so the next audit starts from them (Phase 1)
instead of rediscovering:

```
Use tool: upsert-document
collection_name: "sync_observations"
search_fields: { "_key": "<PROJECT_ID>_<YYYYMMDD_HHMMSS>_<n>" }
document_data: {
  "_key": "<PROJECT_ID>_<YYYYMMDD_HHMMSS>_<n>",
  "project_id": "<PROJECT_ID>",
  "req_id": "<REQ-NNN or null>",
  "observation_type": "spec_gap" | "assumption_violation" | "precision_needed" | "edge_case" | "cross_layer_invariant" | "design_alternative" | "deprecation_signal",
  "summary": "<one line>",
  "detail": "<enough context to act on next audit>",
  "severity": "low" | "medium" | "high",
  "state": "unprocessed",
  "source": "prd-sync",
  "created_at": "<ISO timestamp>"
}
```

Update the project registry (now including the PRD hash from Phase 0, which also powers the
session-start staleness check):

```
Use tool: upsert-document
collection_name: "project_registry"
search_fields: { "_key": "<PROJECT_ID>" }
document_data: {
  "_key": "<PROJECT_ID>",
  "project_id": "<PROJECT_ID>",
  "project_name": "<PROJECT_NAME from CLAUDE.md>",
  "prd_path": "<PRD_FILE>",
  "last_sync": "<ISO timestamp>",
  "open_gaps": <count of MISSING + PARTIAL>,
  "prd_sha256": "<hash from Phase 0>",
  "prd_checked_at": "<ISO timestamp>"
}
update_data: {
  "last_sync": "<ISO timestamp>",
  "open_gaps": <count of MISSING + PARTIAL>,
  "prd_sha256": "<hash from Phase 0>",
  "prd_checked_at": "<ISO timestamp>"
}
```

If MCP is unavailable: emit `[PRD-SYNC] ArangoDB unavailable — drift report is local only.` and continue.

### Phase 5 — Clear drift queue

```bash
rm -f .prd-drift-queue/*
```

### Phase 6 — Review PRD patches + propose fixes

**6a — PRD patch review (user decision required).** Present each `proposed` patch: the
requirement, the observed divergence, the proposed PRD text, the justification. For each:
- **Accept** → edit the PRD file applying the patch, then update the patch document:
  `review_state: "accepted"`, `applied_at: <ISO>`. Re-run the Phase 0 hash and store it.
- **Reject** → `review_state: "rejected"`, and record the rejection as a `sync_observations`
  entry (Phase 4c) so the learning survives — the next audit sees why it was rejected.
- **Defer** → leave `proposed`.

**PRD patches are NEVER auto-applied.** If the session is unattended (no user response), the
default is: leave every patch `proposed`, write the observation, continue — never stall the
audit waiting, and never apply.

**6b — Fix proposals (optional).** For each MISSING requirement, propose a concrete
implementation: which file, what function/class/middleware, any dependency changes.
Do not implement without user confirmation.

---

## Key invariants
- Never claim IMPLEMENTED without `file:line` evidence. A test is not an implementation.
- Evidence must pass `check_evidence.py` (Phase 2.5) before it is persisted — no exceptions,
  no override flag.
- Never skip Phase 2 — even if you believe the code is aligned, grep it.
- OUTDATED-PRD produces a proposed patch, never a silent absorption of the divergence and
  never an unapproved PRD edit.
- If the PRD itself is ambiguous, note the ambiguity in the drift report AND record it as a
  `sync_observations` entry, but do not block the audit.
- Unattended sessions take the recorded defaults (leave proposed, record, continue) — they
  never stall and never auto-apply.
