# Onboarding — team shared memory + drift detection for Claude Code / Cursor

Welcome! Our team runs **one shared ArangoDB** so patterns, drift, and project state are visible to
everyone. This gets you connected in ~10 minutes. In every project you opt in you get:

- **`/pattern-search`** — before solving a problem, check solutions teammates already verified.
- **`/pattern-save`** — after solving something reusable, save it for the whole team.
- **`/prd-sync`** — audit your code against its PRD; open gaps are tracked automatically.

Retrieval is hybrid (semantic + keyword) with a graph of related patterns — you don't need to know
that to use it. `setup.md` is the deep reference + troubleshooting; this is the happy path.

> **You connect to the team's shared cluster — you do NOT run your own database, and you do NOT run any
> `install.py`/schema setup.** The shared memory already exists; you just point your MCP client at it.

---

## Prerequisites (install once)
- **Python 3.11+ and Poetry** (`pipx install poetry` if needed).
- **Shared-cluster credentials** — get these **from your team lead / secrets manager** (never from a
  repo or chat). You'll paste them into your *local* MCP config in step 3; they are never committed.
- **Your own OpenAI API key** — for semantic search (keyword-only still works without one).
- **Claude Code and/or Cursor.**
- (Docker is *not* required — that was only for the old local setup.)

## 1. Clone both repos under `~/code/`
```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/arango-solutions/arango-solutions-mcp.git arango-solutions-mcp-server
git clone https://github.com/ArthurKeen/arango-shared-memory.git
```

## 2. Install the MCP server (runs locally, talks to the shared cluster)
```bash
cd ~/code/arango-solutions-mcp-server && poetry install
```

## 3. Register the MCP server pointing at the shared cluster
Add this under a top-level `"mcpServers"` key in **both** `~/.claude.json` and `~/.cursor/mcp.json`.
Fill in `<you>`, the **credentials you were given out-of-band**, and **your own** OpenAI key:
```json
{
  "arangodb-memory-mcp": {
    "command": "poetry",
    "args": ["run", "python", "main.py"],
    "cwd": "/Users/<you>/code/arango-solutions-mcp-server",
    "env": {
      "ARANGO_HOSTS": "https://prod.demo.pilot.arango.ai:8529",
      "ARANGO_ROOT_USERNAME": "<your shared-cluster username>",
      "ARANGO_ROOT_PASSWORD": "<your shared-cluster password — DO NOT COMMIT>",
      "ARANGO_DEFAULT_DB_NAME": "memory",
      "ARANGO_VERIFY_SSL": "true",
      "OPENAI_API_KEY": "sk-...your own key...",
      "EMBEDDING_MODEL": "text-embedding-3-small"
    }
  }
}
```
These files live in your home directory and are **not** in any repo — keep the credentials there only.
Then **reload Cursor / restart Claude Code** so the tools load. (If `poetry` isn't on the launcher PATH,
use its absolute path from `which poetry`, or `command: .venv/bin/python`, `args: ["main.py"]`.)

## 4. Verify you're connected to the shared memory
```bash
cd ~/code/arango-solutions-mcp-server
poetry run python ~/code/arango-shared-memory/scripts/verify.py
```
You should see the shared host, "ALL CHECKS PASSED", and a non-zero pattern count + several registered
projects (that's the shared state — you're in). **Do not run `install.py` or the `setup_*`/`phase*`
scripts against the shared cluster** — they're for standing up a *new* backend, not joining an existing one.

## 5. Turn a project into a dark-factory project
From anything under `~/code/`:
```bash
~/code/arango-shared-memory/scripts/bootstrap_project.sh --target ~/code/my-project \
  --project-name "My Project" --project-id my-project \
  --project-type web-api --prd-file docs/PRD.md
```
Installs `CLAUDE.md`, the drift hooks, and the three skills (kept in `templates/` — never hand-copy).
Use a **unique `--project-id`** (it namespaces your patterns/drift in the shared store). Add a `PRD.md`,
run `/prd-sync` once for a baseline. Repeat per project.

## 6. Use it
- Starting a non-trivial problem? **`/pattern-search "<what you're stuck on>"`** first.
- Solved something reusable? **`/pattern-save`**.
- Touched implementation files? **`/prd-sync`** at session end (the hook reminds you).

---

## Notes
- **It's shared** — patterns you save are visible to the whole team immediately, and you see theirs.
  Save reusable, non-secret techniques; never put credentials or client-specific data in a pattern.
- **Credentials:** shared-cluster creds and your OpenAI key live *only* in your local MCP config. Never
  commit them; never paste them into a repo, PR, or pattern. `.env` files are gitignored.
- **Stuck?** `setup.md` has a Troubleshooting table (no view, no OpenAI key, `poetry` not on PATH,
  hooks not firing, TLS/auth).
