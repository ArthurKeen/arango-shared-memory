# Onboarding — shared memory + drift detection for Claude Code / Cursor

Welcome! This gets you from zero to a working setup in ~10 minutes. You'll get three
capabilities in **every** project you opt in:

- **`/pattern-search`** — before solving a problem, check solutions teammates already verified.
- **`/pattern-save`** — after solving something reusable, save it for everyone.
- **`/prd-sync`** — audit your code against its PRD; open gaps are tracked automatically.

Retrieval is hybrid (semantic + keyword) with a graph of related patterns — but you don't
need to know any of that to use it. `setup.md` is the deep reference + troubleshooting;
this page is the happy path.

---

## Prerequisites (install once)
- **Docker Desktop** (for a local ArangoDB) — running.
- **Python 3.11+ and Poetry** (`pipx install poetry` if you don't have it).
- **An OpenAI API key** — needed for semantic search. (Without it, everything still works, just
  keyword-only.) You'll paste it into your MCP config in step 4; it is never committed.
- **Claude Code and/or Cursor.**

## 1. Clone both repos under `~/code/`
```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/arango-solutions/arango-solutions-mcp.git arango-solutions-mcp-server
git clone https://github.com/ArthurKeen/arango-shared-memory.git
```

## 2. Install the MCP server
```bash
cd ~/code/arango-solutions-mcp-server && poetry install
```

## 3. Start ArangoDB (Docker) — copy the whole line, the flag matters
```bash
docker run -d --name shared-memory-arangodb --restart unless-stopped \
  -p 8539:8529 -e ARANGO_ROOT_PASSWORD=openSesame \
  -v shared-memory-arango-data:/var/lib/arangodb3 \
  arangodb/arangodb:latest arangod --experimental-vector-index
```
> `--experimental-vector-index` is required for semantic search. Skip it and you're keyword-only.

## 4. Register the MCP server (once, globally)
Add this under a top-level `"mcpServers"` key in **both** `~/.claude.json` (Claude Code) and
`~/.cursor/mcp.json` (Cursor). Replace `<you>` and paste your own OpenAI key:
```json
{
  "arangodb-memory-mcp": {
    "command": "poetry",
    "args": ["run", "python", "main.py"],
    "cwd": "/Users/<you>/code/arango-solutions-mcp-server",
    "env": {
      "ARANGO_HOSTS": "http://localhost:8539",
      "ARANGO_ROOT_USERNAME": "root",
      "ARANGO_ROOT_PASSWORD": "openSesame",
      "ARANGO_DEFAULT_DB_NAME": "memory",
      "OPENAI_API_KEY": "sk-...your key...",
      "EMBEDDING_MODEL": "text-embedding-3-small"
    }
  }
}
```
Then **reload Cursor / restart Claude Code** so the tools load. (If `poetry` isn't found, set
`command` to the absolute path from `which poetry`, or use `.venv/bin/python` with `args: ["main.py"]`.)

## 5. Create the database + schema (once)
```bash
cd ~/code/arango-solutions-mcp-server
poetry run python ~/code/arango-shared-memory/scripts/install.py
```
Green all the way + "ALL CHECKS PASSED" = you're live.

## 6. Turn a project into a dark-factory project
From anything under `~/code/`:
```bash
~/code/arango-shared-memory/scripts/bootstrap_project.sh --target ~/code/my-project \
  --project-name "My Project" --project-id my-project \
  --project-type web-api --prd-file docs/PRD.md
```
This installs `CLAUDE.md`, the drift hooks, and the three skills (kept in `templates/` — never
hand-copy them). Add a `PRD.md`, then run `/prd-sync` once to set the baseline. Repeat per project.

## 7. Use it
- Starting a non-trivial problem? Run **`/pattern-search "<what you're stuck on>"`** first.
- Solved something reusable? **`/pattern-save`**.
- Touched implementation files? **`/prd-sync`** at session end (the hook reminds you).

Check health / adoption anytime:
```bash
cd ~/code/arango-solutions-mcp-server && poetry run python ~/code/arango-shared-memory/scripts/verify.py
```

---

## Notes
- **You're on your own local ArangoDB for now** — your memory is private to you. The team will move
  to a **shared** ArangoDB so patterns are visible to everyone; when that happens it's a one-line
  config change (point `ARANGO_HOSTS` at the shared host). No action needed from you yet.
- **Stuck?** `setup.md` has a Troubleshooting table covering the common traps (missing flag, no
  OpenAI key, `poetry` not on PATH, hooks not firing).
- **Secrets:** your OpenAI key lives only in your MCP config; `.env` files are gitignored. Don't commit keys.
