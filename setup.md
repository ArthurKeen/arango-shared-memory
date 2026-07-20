# Multi-Project Workflow Automation — Setup & Onboarding

This system gives Claude Code (and Cursor) three capabilities across all your projects:

1. **PRD drift detection** (`/prd-sync`) — audits code against its PRD, classifies every requirement
   IMPLEMENTED / PARTIAL / MISSING / TEST-ONLY, writes open gaps to `drift_alerts`, closes them when
   fixed. A PostToolUse hook queues a reminder whenever an implementation file is edited; a Stop hook
   nudges at session end if changes are unsynced.
2. **Shared solution memory** (`/pattern-search`, `/pattern-save`) — before solving a non-trivial
   problem, search verified solutions from *any* project; after solving something reusable, save it.
   Retrieval is **hybrid** (semantic vector + BM25 keyword, RRF-fused, re-ranked by importance ·
   recency · usage) with an optional **graph** layer of related-pattern links — all executed
   server-side, so agents pass text and get ranked results (never raw vectors).
3. **Project registry + read-path analytics** — `project_registry` tracks each project; `search_log`
   records every search (query, hit, project) so reuse is measurable, not assumed.

All skills degrade gracefully when ArangoDB / the MCP is unreachable, and fall back to keyword-only
(BM25) when embeddings aren't configured.

> **Single source of truth:** the CLAUDE.md, hooks, and three skills live in `templates/` and are
> installed by `scripts/bootstrap_project.sh`. **Do not hand-copy skill bodies** — that is exactly how
> earlier docs drifted out of sync. This document references the templates rather than duplicating them.

---

## Prerequisites
- **Python 3.11+ and Poetry**, and **Claude Code and/or Cursor** — everyone.
- **Shared-cluster credentials** (from your team lead) — for the common "join the shared memory" path.
- **Your own OpenAI API key** for embeddings (hybrid/graph). Optional — without it the system is keyword-only.
- **Docker** — *admin only*, for standing up a new local backend (STEP 0). Not needed to join the shared cluster.
- The ArangoDB server **must be started with `--experimental-vector-index`** for hybrid/graph search
  (the shared cluster already is; relevant only if you stand up a new backend).

## Two repositories (clone both under `~/code/`)
```bash
git clone https://github.com/arango-solutions/arango-solutions-mcp.git ~/code/arango-solutions-mcp-server
# and this repo:
git clone <arango-shared-memory remote> ~/code/arango-shared-memory
```
- **arango-solutions-mcp-server** — the FastMCP server exposing the `arangodb-memory-mcp` tools
  (`pattern-search`, `save-pattern`, `embed-document`, `execute-aql-query`, …).
- **arango-shared-memory** (this repo) — setup/phase scripts, project templates, docs.

---

## Which path are you on?
- **Joining the team's shared memory (most people):** the database already exists on the shared
  cluster — get credentials from your team lead. Do **STEP 1, 3, 4, 5** and **skip STEP 0 and STEP 2**.
  Do **not** run `install.py` / `setup_*` / `phase*` against the shared cluster (those stand up a *new*
  backend, and need admin/root). The teammate happy-path is **[ONBOARDING.md](ONBOARDING.md)**.
- **Admin, standing up a NEW backend** (a fresh shared cluster, or a private local one for solo/offline
  use): do **STEP 0 → 1 → 2 → 3 → 4**, then provision teammates (see "Shared deployment").

---

## STEP 0 (admin / new backend only) — ArangoDB (Docker, run once)
The shared memory uses its own ArangoDB CE container on host port **8539** (so it never collides with
another ArangoDB on 8529). **The `arangod --experimental-vector-index` flag is required** — without it,
vector-index creation fails and the system silently stays keyword-only.

```bash
docker run -d --name shared-memory-arangodb --restart unless-stopped \
  -p 8539:8529 -e ARANGO_ROOT_PASSWORD=openSesame \
  -v shared-memory-arango-data:/var/lib/arangodb3 \
  arangodb/arangodb:latest arangod --experimental-vector-index
```
Confirm: `curl -s -u root:openSesame http://localhost:8539/_api/version`

## STEP 1 — Install the server
```bash
cd ~/code/arango-solutions-mcp-server && poetry install
```
This creates the server's virtualenv (has `python-arango`, `rdflib`, etc.). All scripts below run via
`poetry run python …` from this directory.

## STEP 2 (admin / new backend only) — Create the schema (run once)
> **Teammates joining the existing shared memory SKIP this** — the schema is already there, and
> `install.py` needs admin/root anyway. This is only for standing up a new backend.

One idempotent command creates the `memory` database, collections
(`shared_patterns`, `project_registry`, `drift_alerts`, `search_log`), indexes, and the
`patterns_search` BM25 view + graded-scoring fields:
```bash
poetry run python ~/code/arango-shared-memory/scripts/install.py
```
(`install.py` runs `setup_schema.py` → `phase1_setup.py` → `verify.py`. It is safe to re-run. Pass
`--with-embeddings` to also run `phase1b`/`phase2` once you have a key + at least one pattern.)

## STEP 3 — Register the MCP server (globally, once per tool)
Register under the id **`arangodb-memory-mcp`** in *both* Claude Code (`~/.claude.json`) and Cursor
(`~/.cursor/mcp.json`), under a top-level `"mcpServers"` key:
```json
{
  "command": "poetry",
  "args": ["run", "python", "main.py"],
  "cwd": "/Users/<you>/code/arango-solutions-mcp-server",
  "env": {
    "ARANGO_HOSTS": "https://<shared-cluster-host>:8529",
    "ARANGO_ROOT_USERNAME": "<your username — from your team lead>",
    "ARANGO_ROOT_PASSWORD": "<your password — DO NOT COMMIT>",
    "ARANGO_DEFAULT_DB_NAME": "memory",
    "ARANGO_VERIFY_SSL": "true",
    "OPENAI_API_KEY": "sk-...your own key...",
    "EMBEDDING_MODEL": "text-embedding-3-small"
  }
}
```
- Values above are for **joining the shared cluster** (the common case). For a **new local backend**
  instead, use `"ARANGO_HOSTS": "http://localhost:8539"`, `root` / `openSesame`, and omit `ARANGO_VERIFY_SSL`.
- `OPENAI_API_KEY` enables hybrid/graph. Omit it to run keyword-only. **Never commit this file / key.**
- If `poetry` isn't on the launcher PATH, use the absolute path (`which poetry`) or point `command` at
  `.venv/bin/python` with `args: ["main.py"]`.
- Reload Cursor / restart Claude Code so the tools load.

## STEP 4 — Verify
```bash
cd ~/code/arango-solutions-mcp-server
poetry run python ~/code/arango-shared-memory/scripts/verify.py
```
Green across connectivity, collections, indexes, round-trip, and the `patterns_search` view; the
**read-path scorecard** shows searches/hit-rate once you start using it. Exit 0 = healthy.

## STEP 5 — Bootstrap each project
From the project you want to instrument:
```bash
~/code/arango-shared-memory/scripts/bootstrap_project.sh --target ~/code/my-api \
  --project-name "My API" --project-id my-api \
  --project-type web-api --prd-file docs/PRD.md --tech-stack "TypeScript, Node.js"
```
This installs (from `templates/`, filling placeholders) and git-ignores the personal infra:
- `CLAUDE.md` — project identity + the mandatory `/pattern-search → solve → /pattern-save → /prd-sync` protocol
- `.claude/settings.json` — the drift hooks (PostToolUse queues on code edits; Stop nudges at session end)
- `.claude/skills/{prd-sync,pattern-save,pattern-search}/` — the three skills (current versions)
- `.cursor/rules/workflow.mdc` — the Cursor equivalent

Re-running is safe (skips existing; `--force` overwrites). Then create the project's `PRD.md` and run
`/prd-sync` to establish its drift baseline. Because `arangodb-memory-mcp` is registered *globally*,
every bootstrapped project can reach shared memory with no per-project MCP wiring.

## Enabling hybrid + graph (if you skipped it in STEP 2)
1. Put `OPENAI_API_KEY` + `EMBEDDING_MODEL` in the MCP env (STEP 3) and reload.
2. Save at least one pattern (`/pattern-save`).
3. `poetry run python .../phase1b_setup.py` (embeddings + vector index) then `.../phase2_setup.py`
   (graph edges). `phase2b_extract.py` (gpt-4o LLM edges) and `phase3_lifecycle.py` (supersede/TTL) are
   periodic maintenance, not required for daily use.

---

## Shared deployment (team) — local → shared ArangoDB
The value multiplier is a **single shared `memory` DB** so patterns/drift are visible across the whole
team, not just across one person's projects. Because every script and the MCP tool resolve the host
from env, switching is a **config change**: set each teammate's `arangodb-memory-mcp` env
`ARANGO_HOSTS` (and credentials) to the shared host, then run `install.py` once against it.

Checklist for the shared server:
- Start ArangoDB with `--experimental-vector-index` (ops-owned).
- Real credentials + TLS (`https://…`, `ARANGO_VERIFY_SSL=true`) — retire `openSesame`.
- Run `install.py` once against the shared host to create schema + view.
- Keep the OpenAI key in each teammate's MCP env (or centralize embedding behind the shared server).

**Rehearsed against a real 3.12.9 cluster (2026-07-16)** — `install.py` (schema + BM25 view +
round-trip) worked over TLS with real auth on the first try. Two remote-only gotchas surfaced that do
**not** appear on fast local single-server, so plan for them when running `phase1b_setup.py` on the shared box:
- **Vector-index build outlasts the client timeout.** `add_index` for the cosine index exceeds
  python-arango's default 60 s read timeout (FAISS training + latency); the client raises `ReadTimeout`
  but the index *is* created server-side. Use a longer `request_timeout` and verify with `list-indexes`
  rather than trusting the call to return.
- **"Not yet trained" window.** Immediately after, `APPROX_NEAR_COSINE` can fail with
  `ERR 1555 (not yet trained)` briefly — poll until a trivial vector query succeeds before relying on it.
  (`phase1b_setup.py` should be given a longer timeout / retry when pointed at a shared cluster.)

### Provisioning teammates (per-developer users)
Give each developer **their own** least-privilege user — better attribution ("who did what"),
clean offboarding, and no shared password to rotate. The admin runs (root creds resolved from their
own MCP config):
```bash
poetry run python scripts/add_teammate.py <username>          # create: rw on `memory` only, prints creds once
poetry run python scripts/add_teammate.py <username> --revoke # offboard: deactivate + revoke
```
Hand the printed credentials to the developer **out-of-band** (never commit them); they go only in
that developer's local MCP config. The user gets `rw` on `memory` and **no access** to other databases
on the shared cluster (verified at creation).

**Recommended:** direct shared writes (everyone's MCP → the one shared DB). **Not recommended:**
local-arango-per-person *syncing* into a shared one — it adds sync lag and cross-instance
merge/dedup complexity for no benefit on a networked team. If you want private experimentation, use a
separate local `memory` DB and switch to the shared one via env — two databases, not a sync pipeline.

---

## Troubleshooting
| Symptom | Cause | Fix |
|---|---|---|
| `pattern-search` errors / returns nothing | `patterns_search` view missing | run `install.py` (or `phase1_setup.py`) |
| Everything keyword-only; no semantic hits | no `OPENAI_API_KEY`, or arangod lacks `--experimental-vector-index` | add the key (STEP 3) + recreate the container with the flag (STEP 0), then `phase1b_setup.py` |
| Vector index creation fails (`ERR 10`) | server not started with `--experimental-vector-index` | recreate the container with the flag; data persists in the named volume |
| `add_index` raises `ReadTimeout` on a remote cluster | FAISS training + latency exceeds the 60s client timeout | raise `request_timeout`; the index is still created server-side — verify with `list-indexes` |
| `ERR 1555 vector index is not yet trained` | queried a just-created index before training finished (remote) | poll/retry `APPROX_NEAR_COSINE` until it succeeds |
| Drift hook never fires | stale hook reading `$CLAUDE_TOOL_INPUT` | re-bootstrap (current hook reads stdin/`tool_input`); Cursor doesn't run Claude Code hooks — expected |
| MCP server won't start | `poetry` not on the launcher PATH | use absolute poetry path, or `command: .venv/bin/python`, `args: ["main.py"]` |
| `ERR 1521 collection not known to traversal` | cluster traversal missing `WITH` | add `WITH <all reachable vertex collections>` (needed on cluster, hidden on single-server) |
| `ERR 1579 access after data-modification` | one AQL reads a collection after modifying it | split into separate statements |
| Saving a pattern fails: `Expecting type Array` | inserting into a vector-indexed collection without the embedding | use `save-pattern` (embeds then inserts); don't insert-then-embed |
