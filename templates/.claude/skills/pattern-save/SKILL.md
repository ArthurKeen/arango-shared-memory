# Pattern Save Skill

## Invocation
`/pattern-save` — capture a verified solution (or other memory) to shared ArangoDB memory.

## Purpose
After solving a problem that would recur in other projects, write it to `shared_patterns`
so `/pattern-search` can retrieve it in any project.
Only save verified successes — never save speculative solutions.

---

## Protocol

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

### Phase 1 — Duplicate check

```
Use tool: execute-aql-query
query: FOR p IN shared_patterns
         FILTER p.problem_category == @cat AND p.project_type == @ptype
         SORT p.created_at DESC LIMIT 5
         RETURN { key: p._key, desc: p.problem_description, solution: p.solution_summary }
bindVars: { cat: "<problem_category>", ptype: "<project_type>" }
```

If a pattern with >70% semantic overlap exists, present it and ask:
- **Update** the existing one with new details
- **Create** new (solution is meaningfully different)
- **Skip** (essentially the same)

**Update path:** merge the changed fields with `upsert-document` — and if you changed
`problem_description` or `solution_summary`, ALSO set `"embedding_pending": true` in the same
update. The stored embedding no longer matches the text; the pending flag makes the next
maintenance pass (`scripts/maintain.py` / `phase1b_setup.py`) re-embed it. Never leave an edited
memory with a stale vector.

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
tags:                ["<tag1>", "<tag2>"]
importance:          <1-10>
source_file:         "<relevant file:line if applicable>"
```
Returns `{ _key, embedded, relates_edges, superseded }`. The tool sets `usage_count=0`,
`last_used=created_at`, and a timestamped `_key` automatically. `importance` / `usage_count` /
`last_used` feed the `/pattern-search` graded scoring; `/pattern-search` bumps `usage_count` and
refreshes `last_used` when a pattern is applied.

**Phase 2b — attach the taxonomy fields.** Immediately after `save-pattern` returns, merge the
memory-type metadata onto the new document (one call; `save-pattern` may not pass unknown fields
through, and a merge on an existing doc does not touch the embedding, so this is always safe):
```
Use tool: upsert-document
collection_name: "shared_patterns"
search_fields: { "_key": "<the _key save-pattern returned>" }
document_data: { "_key": "<same>" }
update_data: { "memory_type": "<pattern|feedback|user|project|reference>",
               "why": "<feedback only>", "how_to_apply": "<feedback only>" }
```
Omit `why`/`how_to_apply` for non-`feedback` types.

- On success: `[PATTERN-SAVE] Saved <_key> (<memory_type>, relates_edges=<n>).`
- If it returns an error mentioning "embedding required": OPENAI_API_KEY is unset/unreachable and the
  collection has a vector index — saving is blocked until embeddings are available. Report and stop.
- If `save-pattern` is unavailable (older server): fall back to the appendix insert flow, but note it
  fails while the vector index is present.

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
