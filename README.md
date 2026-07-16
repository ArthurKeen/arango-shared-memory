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

Full design + step-by-step onboarding: **[setup.md](setup.md)**. New teammate? Start there.

## Two repositories (you need both)

| Repo | Role | Get it |
|---|---|---|
| **arango-solutions-mcp-server** | The MCP server (the `arangodb-memory-mcp` tools: `pattern-search`, `save-pattern`, `embed-*`, AQL, etc.) | `git clone https://github.com/arango-solutions/arango-solutions-mcp.git ~/code/arango-solutions-mcp-server` |
| **arango-shared-memory** (this repo) | Setup/phase scripts, project templates, docs | clone alongside it under `~/code/` |

## Prerequisites
- **Docker** (local ArangoDB), or access to a shared ArangoDB 3.12.4+ started with `--experimental-vector-index`.
- **Python 3.11+ and Poetry** (the server installs its deps via `poetry install`).
- **An OpenAI API key** — required for the hybrid/graph features (server-side embeddings). Without it the
  system still runs, keyword-only (BM25).
- **Claude Code and/or Cursor.**

## Quick start (local, one time)

```bash
# 0. Clone both repos under ~/code (see table above), then install server deps:
cd ~/code/arango-solutions-mcp-server && poetry install

# 1. Start the dedicated ArangoDB — NOTE the --experimental-vector-index flag (required for hybrid/graph):
docker run -d --name shared-memory-arangodb --restart unless-stopped \
  -p 8539:8529 -e ARANGO_ROOT_PASSWORD=openSesame \
  -v shared-memory-arango-data:/var/lib/arangodb3 \
  arangodb/arangodb:latest arangod --experimental-vector-index

# 2. Register the MCP server globally (see setup.md STEP 3) with your OpenAI key in its env.
#    Then reload Cursor / restart Claude Code so the tools load.

# 3. Create the schema + BM25 view + scorecard (idempotent, one-shot):
poetry run python ~/code/arango-shared-memory/scripts/install.py

# 4. Bootstrap a project (installs the CURRENT skills/hooks from templates/ — never hand-copy them):
~/code/arango-shared-memory/scripts/bootstrap_project.sh --target ~/code/my-api \
  --project-name "My API" --project-id my-api --project-type web-api --prd-file docs/PRD.md

# 5. Verify anytime:
poetry run python ~/code/arango-shared-memory/scripts/verify.py
```

`verify.py` checks connectivity, collections, indexes, a write→read→delete round-trip, and prints the
**adoption + read-path scorecard** (patterns, projects, drift, searches logged, hit rate). Exit 0 = healthy.

## Enabling hybrid + graph (after step 3)
The installer sets up keyword search immediately. To turn on semantic/vector + graph:
1. Ensure `OPENAI_API_KEY` (+ `EMBEDDING_MODEL`, default `text-embedding-3-small`) is in the MCP env.
2. Save at least one pattern (`/pattern-save` in a project).
3. Run `phase1b_setup.py` (embeddings + vector index) then `phase2_setup.py` (graph edges) — or
   `install.py --with-embeddings`. `phase2b_extract.py` / `phase3_lifecycle.py` are periodic maintenance.

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

## Moving to a shared ArangoDB (team)
Everything resolves the DB host from env, so going from local to shared is a **config change, not a
code change**: point each teammate's `arangodb-memory-mcp` env `ARANGO_HOSTS` at the shared host
(with real credentials + TLS, and the `--experimental-vector-index` flag enabled server-side). See
setup.md "Shared deployment."
