# Pattern Save Skill

## Invocation
`/pattern-save` — capture a verified solution (or other memory) to shared ArangoDB memory.

## Purpose
After solving a problem that would recur in other projects, write it to `shared_patterns`
so `/pattern-search` can retrieve it in any project.
Only save verified successes — never save speculative solutions.

---

## Protocol

### Phase 0a — Triage the capture queue (when present)

If `.pattern-capture-queue/` contains `*.json` files, the Stop hook mined candidate memories
from earlier sessions (resolved command failures, user corrections). Before saving anything new,
triage them — this is where LLM judgment enters; the hook deliberately has none:

1. Read each queue file. For every candidate decide: **save** (a real, reusable lesson —
   continue with the normal flow below; corrections usually become `feedback` memories with
   why + how_to_apply), or **discard** (session-specific noise, transient env issues, or
   something shared memory already has — check with /pattern-search first).
2. Delete each queue file once its candidates are dispatched (`rm .pattern-capture-queue/<f>.json`).
   Never leave triaged files behind; the SessionStart digest nags about them until gone.
3. Quality bar is unchanged: only verified, reusable lessons get saved. An empty triage
   (all discarded) is a perfectly good outcome.

### Phase 0 — Gather context

First decide the **memory type** (infer from context; ask only if genuinely ambiguous):
- `pattern` (default) — a solved problem + verified solution
- `feedback` — a correction the user gave you, OR an approach the user explicitly confirmed
  (save successes too — otherwise future sessions drift away from validated approaches).
  REQUIRES two extra fields: **why** (the reason behind the guidance) and
  **how_to_apply** (concrete instructions for applying it next time).
- `user` — durable facts about who the user is (role, expertise, preferences)
- `project` — in-flight project state, decisions, constraints (convert relative dates to absolute)
- `reference` — pointer to an external system/dashboard/document

Then gather (ask the user if not already clear from context):
1. **Problem category**: `auth` | `api-design` | `state-management` | `prd-drift` | `testing` | `deployment` | `data-model` | `performance` | `other`
   (for non-`pattern` types, use the closest fit or `other`)
2. **Problem description**: one sentence (for `feedback`: the situation the guidance applies to)
3. **Solution summary**: 2-5 sentences, specific enough to apply in a different project
   (for `feedback`: the guidance itself)
4. **Tags**: 2-5 keywords

Infer from `AGENTS.md` (or `CLAUDE.md`) without asking: `project_id`, `project_type`, `project_name`.

Also rate **importance** 1–10 yourself (no need to ask): how broadly reusable / high-impact is this
pattern? `1` = mundane/project-specific, `10` = a technique many projects will need. This drives
ranking in `/pattern-search` (it replaced the old `worked`-only signal).

### Phase 1 — Duplicate awareness (the server enforces it)

The server enforces write-time consolidation, so a manual pre-check is optional. If you want
early awareness, run `/pattern-search "<the problem>"` first — but the real gate is Phase 2:
`save-pattern` refuses to insert when an existing **valid** memory is ≥0.80 cosine-similar and
returns the candidates instead (`consolidation_required: true`). When that happens, DECIDE:

- **Update** the existing memory (new details, same lesson): merge fields with `upsert-document`
  — and if you changed `problem_description` or `solution_summary`, ALSO set
  `"embedding_pending": true` in the same update so maintenance re-embeds it. Never leave an
  edited memory with a stale vector.
- **Replace** it (the old memory is now wrong/obsolete): re-call `save-pattern` with
  `supersedes_key: "<candidate _key>"` — the old one is invalidated bi-temporally
  (valid_to closed, demoted from ranking) and linked via `pattern_supersedes`. History is kept:
  `pattern-search(as_of=...)` can still see what was believed before.
- **Force** (genuinely different despite the similarity): re-call with `force: true`.

Tell the user which you chose and why — that judgment is the whole point of the gate.

### Phase 2 — Write pattern (embed-THEN-insert, single tool)

Use the `save-pattern` tool. It embeds the text and inserts the document WITH its embedding in one
server-side step, then maintains the graph (`pattern_relates_to` + supersede check). This is required
because `shared_patterns` has a non-sparse vector index: a plain insert without the embedding is
rejected ("Expecting type Array"), so you MUST NOT insert first and embed later.

```
Use tool: save-pattern
problem_description: "<one-sentence>"
solution_summary:    "<2-5 sentences>"
problem_category:    "<category>"
project_id:          "<PROJECT_ID from AGENTS.md>"
project_type:        "<project_type from AGENTS.md>"
memory_type:         "<pattern|feedback|user|project|reference>"
why:                 "<feedback only: the reason behind the guidance>"
how_to_apply:        "<feedback only: how to apply it next time>"
tags:                ["<tag1>", "<tag2>"]
importance:          <1-10>
source_file:         "<relevant file:line if applicable>"
```
The taxonomy is stamped server-side — do NOT do a separate post-save merge for
`memory_type`/`why`/`how_to_apply` (the old Phase 2b; it was routinely skipped and left
memories typeless). Omit `why`/`how_to_apply` for non-`feedback` types.

If the response is `consolidation_required: true`, go back to Phase 1 and decide
(update / supersedes_key / force) — nothing was saved yet.
Returns `{ _key, embedded, relates_edges, superseded }`. The tool sets `usage_count=0`,
`last_used=created_at`, and a timestamped `_key` automatically. `importance` / `usage_count` /
`last_used` feed the `/pattern-search` graded scoring; `/pattern-search` bumps `usage_count` and
refreshes `last_used` when a pattern is applied.

- On success: `[PATTERN-SAVE] Saved <_key> (<memory_type>, relates_edges=<n>).`
- On `consolidation_required: true`: nothing was saved — decide per Phase 1
  (update / `supersedes_key` / `force`) and act.
- If `embedding_pending: true` in the response: OpenAI was unreachable; the memory saved with a
  deferred vector and is keyword-searchable now (maintenance re-embeds it). Nothing to do.
- If `save-pattern` is unavailable (older server): fall back to the appendix insert flow, but note it
  fails while the vector index is present — and you must merge `memory_type` manually in that case.

> LLM-derived edges (`pattern_addresses_requirement`, `requirement_depends_on`) are NOT built per-save
> — they run as a periodic batch via `scripts/phase2b_extract.py`.

### Phase 3 — Update project registry

```
Use tool: upsert-document
collection_name: "project_registry"
search_fields: { "_key": "<PROJECT_ID>" }
document_data: {
  "_key": "<PROJECT_ID>",
  "project_id": "<PROJECT_ID>",
  "project_name": "<PROJECT_NAME>",
  "project_type": "<project_type>",
  "prd_path": "<PRD_FILE>",
  "patterns_contributed": 1
}
update_data: {
  "patterns_contributed": "<current count + 1>"
}
```

---

## Do not save
- Solutions too specific to this codebase's internal structure
- Workarounds for dependency bugs (file an issue instead)
- Unverified solutions
- Credentials, secrets, or client-specific confidential data — in ANY field, ever

## Appendix — fallback if `save-pattern` is absent (older server)
Only works when `shared_patterns` has NO vector index (otherwise the insert is rejected). Insert the
doc, then embed it server-side:
```
Use tool: upsert-document   collection_name: "shared_patterns"
  search_fields: { "_key": "<PROJECT_ID>_<category>_<YYYYMMDD_HHMMSS>" }
  document_data: { ...fields..., "importance": <1-10>, "usage_count": 0, "last_used": "<ISO>" }
Use tool: embed-document    collection_name: "shared_patterns"   document_key: "<the _key>"
```
Then run `scripts/phase2_setup.py` to build its graph edges.
