# arango-shared-memory

A multi-project workflow-automation system that gives Claude Code and Cursor three
cross-project capabilities, backed by ArangoDB:

1. **PRD drift detection** (`/prd-sync`) — audits a codebase against its PRD, classifies every
   requirement IMPLEMENTED / PARTIAL / MISSING / TEST-ONLY, tracks open gaps in `drift_alerts`.
2. **Shared solution memory** (`/pattern-search`, `/pattern-save`) — a `shared_patterns` store of
   verified solutions usable from any project. Retrieval is **hybrid** (semantic vector + BM25
   keyword, fused and re-ranked) with an optional **graph** layer of related-pattern links, all
   server-side.
3. **Project registry + read-path analytics** — `project_registry` tracks each project's state;
   `search_log` records every search so you can measure whether memory is actually being *reused*.

**New teammate? Start with [ONBOARDING.md](ONBOARDING.md)** — cold start to live in ~10 minutes.
Full design, shared-deployment guidance, and troubleshooting live in **[setup.md](setup.md)**.

## Two repositories (you need both)

| Repo | Role | Get it |
|---|---|---|
| **arango-solutions-mcp-server** | The MCP server (the `arangodb-memory-mcp` tools: `pattern-search`, `save-pattern`, `embed-*`, AQL, etc.) | `git clone https://github.com/arango-solutions/arango-solutions-mcp.git ~/code/arango-solutions-mcp-server` |
| **arango-shared-memory** (this repo) | Setup/phase scripts, project templates, docs | clone alongside it under `~/code/` |

> **The team runs one shared ArangoDB** (the memory already exists on it). Joining that shared memory
> is the common case — you do **not** stand up your own database or run any schema setup. The
> local/standalone path is the *admin* section below.

## Quick start — join the team's shared memory (the common case)
Prereqs: Python 3.11+ & Poetry, Claude Code and/or Cursor, your own OpenAI API key, and the shared-cluster
credentials (get these from your team lead — never from a repo). Then:
```bash
# 1. Clone both repos under ~/code (see table above); install the server:
cd ~/code/arango-solutions-mcp-server && poetry install

# 2. Register the MCP server (id `arangodb-memory-mcp`) in ~/.claude.json AND ~/.cursor/mcp.json,
#    pointing ARANGO_HOSTS at the shared cluster, with your creds + your own OpenAI key.
#    Exact JSON: setup.md STEP 3. Then reload Cursor / restart Claude Code.

# 3. Verify you're connected to the shared memory (should show a non-zero pattern count):
cd ~/code/arango-solutions-mcp-server && poetry run python ~/code/arango-shared-memory/scripts/verify.py

# 4. Bootstrap each project (installs the CURRENT skills/hooks from templates/ — never hand-copy them):
~/code/arango-shared-memory/scripts/bootstrap_project.sh --target ~/code/my-api \
  --project-name "My API" --project-id my-api --project-type web-api --prd-file docs/PRD.md
```
**Do NOT run `install.py` / `setup_*` / `phase*` against the shared cluster** — those stand up a *new*
backend, not join an existing one. Full walkthrough: **[ONBOARDING.md](ONBOARDING.md)**.

## Admin — stand up a NEW backend
Only when creating a fresh shared memory (a new cluster, or a private local one for solo/offline use).

```bash
cd ~/code/arango-solutions-mcp-server && poetry install

# Local Docker instance — NOTE the --experimental-vector-index flag (required for hybrid/graph):
docker run -d --name shared-memory-arangodb --restart unless-stopped \
  -p 8539:8529 -e ARANGO_ROOT_PASSWORD=openSesame \
  -v shared-memory-arango-data:/var/lib/arangodb3 \
  arangodb/arangodb:latest arangod --experimental-vector-index
# (For a hosted cluster instead: skip Docker; just target its host in the env below.)

# Register the MCP with admin creds + OpenAI key (setup.md STEP 3), reload, then create schema+view:
poetry run python ~/code/arango-shared-memory/scripts/install.py
```
- **Hybrid + graph:** with `OPENAI_API_KEY` set and ≥1 saved pattern, run `phase1b_setup.py` (embeddings +
  vector index) then `phase2_setup.py` (graph edges), or `install.py --with-embeddings`.
  `phase2b_extract.py` / `phase3_lifecycle.py` are periodic maintenance.
- **Provision teammates:** `scripts/add_teammate.py <username>` creates a least-privilege user (rw on
  `memory` only) and prints creds to hand out; `--revoke` offboards. See setup.md "Shared deployment."
- **Going local → shared** is a config change, not a code change (env `ARANGO_HOSTS` + real creds/TLS +
  the vector flag enabled server-side).

`verify.py` (either path) checks connectivity, collections, indexes, a round-trip, and prints the
**adoption + read-path scorecard** (patterns, projects, drift, searches, hit rate). Exit 0 = healthy.

## Repository layout
```
setup.md                       Full design + onboarding (canonical)
scripts/
  install.py                   One-shot: schema + view + verify (+ optional embeddings/graph)
  setup_schema.py              Collections + indexes (idempotent)
  phase1_setup.py              patterns_search view + graded-scoring fields
  phase1b_setup.py             Embeddings + cosine vector index
  phase2_setup.py              Graph: similarity + provenance edges
  phase2b_extract.py           LLM-extracted edges (gpt-4o; periodic)
  phase3_lifecycle.py          Supersede / TTL / staleness (periodic)
  verify.py                    Health check + adoption/read-path scorecard
  bootstrap_project.sh         Scaffold a project from templates/
templates/                     Source of truth for CLAUDE.md, hooks, and the 3 skills
```

## More
Design, shared-deployment operations, teammate provisioning, and a troubleshooting table:
**[setup.md](setup.md)**. Teammate happy-path: **[ONBOARDING.md](ONBOARDING.md)**.
