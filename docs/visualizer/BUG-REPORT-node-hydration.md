# Bug report: Graph Visualizer renders canvas nodes as attribute-less stubs (with an editable Save panel)

- **Date:** 2026-08-25
- **Reporter:** Arthur Keen (evidence gathered jointly with a coding agent; every claim
  below is labelled *verified* or *unknown*)
- **Deployment:** `prod.demo.pilot.arango.ai` platform UI, database `memory`, graph
  `memory_graph`; ArangoDB 3.12.9-1 enterprise
- **Frontend bundle at time of report:** `/ui/assets/index-ChTcOCAy.js` (5.6 MB),
  `/ui/assets/vendor-ui-y7sBieqw.js`
- **Severity:** display bug with a **data-loss hazard** (see §5)

## 1. Symptom (verified)

Nodes on the Graph Visualizer canvas carry no document attributes:

- Node labels render the `_id` even when the active theme sets
  `labelAttribute: "req_id"` (field populated 379/379 in the collection).
- The node **Properties panel** shows exactly two fields, `_id` and `_key`, both
  containing the *same* value — the document `_id`
  (e.g. `_key = drift_alerts/r2g_P6-7-E1-ROUTING`). A `_key` can never contain `/`
  (rejected on insert, error 1221), so this object cannot be a stored document: it is
  synthesized client-side from a node-reference string.
- Attribute-based theme rules never match, for any operator — there is no attribute on
  the canvas node to test.

## 2. Control experiment (verified — the decisive evidence)

Same browser, same login, same moment:

| View | Result |
|---|---|
| Visualizer → click node → Properties | 2-field stub, illegal prefixed `_key` |
| Platform Collections UI → `drift_alerts` → same document | fully populated (9–15 fields: `status`, `req_id`, `classification`, `detected_at`, …) |

This eliminates data loss, permissions, and user-identity differences. The defect is
inside the visualizer's node-data path.

## 3. Server-side verification (verified)

- Full-collection audit: all 379 `drift_alerts` docs carry 9–15 non-system fields; core
  fields (`status`, `req_id`, `classification`, `project_id`, `requirement`,
  `detected_at`) populated 379/379.
- `DECODE_REV` audit: the specific stub-rendered documents were last written weeks
  before observation (e.g. `network-asset-management-demo_FR4-4` → 2026-08-01T18:17Z),
  ruling out recent rewrites.
- The gharial vertex API returns the full document:
  `GET /_db/memory/_api/gharial/memory_graph/vertex/drift_alerts/<key>` → all fields.
- The frontend's own hydration query (see §4), replicated verbatim over `_api/cursor`
  with the same credentials, returns fully-populated nodes
  (5 edge ids → 5 edges + 6 full nodes).
- Permissions: every relevant user (`arthur`, `pj`, `engineering`, `burleson`, `root`)
  has `rw` on both database `memory` and collection `drift_alerts`.
- The visualizer's *Settings → Label / Hover* dialog lists the collection's full
  attribute set with types (including fields present on as few as 3–8 docs), so the
  frontend **can** read collection schema server-side. The gap is specifically the
  attribute payload of loaded canvas nodes.

## 4. Frontend internals (verified by reading the shipped bundle)

`index-ChTcOCAy.js` contains a node-hydration helper (`fetchDocumentsByIds`) built
around this AQL template (searchable via `UNION_DISTINCT`):

```aql
LET edges = DOCUMENT(@edgeIds)[* FILTER CURRENT != null]
LET nodes = DOCUMENT(UNION_DISTINCT(edges[*]._from, edges[*]._to, @nodeIds))[* FILTER CURRENT != null]
RETURN { edges, nodes }
```

Immediately preceding it:

```js
if (!a) return { vertices: [], edges: [] };   // a = database handle from context
```

i.e. if the database handle is absent/mismatched, hydration **silently returns empty**
and the canvas keeps stub nodes synthesized from edge `_from`/`_to` strings — consistent
with everything in §1–§3. (Note: a `/gral/.../v1/loaddata` API with `vertex_attributes`
also exists in the bundle, but belongs to the Graph Analytics / Agentic AI Suite path,
not this canvas — eliminated as a suspect.)

## 5. Hazard (verified UI state; outcome untested — deliberately)

The Properties panel renders the stub **editable**, with *+ Add Property* and **Save**
buttons. If Save writes the displayed object back, a fully-populated document would be
replaced by a 2-field stub (or the write fails on the illegal key — untested; we did not
press Save against production data). Until fixed, we are advising the team to treat the
visualizer as read-only and edit documents only via the Collections UI or AQL.

## 6. Additional anomaly (observed; mechanism unknown)

With three string-equality rules present in the active theme
(`status = open|undocumented|closed` → red/amber/grey), **all 366 drift_alert nodes
rendered red** (the first rule's colour) while only 163 were open — despite nodes
carrying no `status` attribute to match on. With the rules removed, all nodes render
the base colour. We have no verified mechanism for this and flag it for whoever owns
the rule-evaluation code. Relatedly: the correct wire format for a *string-equality*
rule (`condition.op`) is undocumented and could not be reverse-engineered here, because
the attempt to author one through the UI targeted the theme marked `isDefault: true` —
and the UI cannot save edits to a default theme (a separate, previously documented
limitation), so the authored rule was silently discarded. Re-attempting on a
non-default theme is the known path to recovering the format.

## 6b. UPDATE 2026-08-26/28 — failure is SIZE-DEPENDENT (user-verified); §6 anomaly explained at the observation level

- On a **smaller canvas** (~260 nodes / 244 edges, loaded via a filtered query) the same
  `drift_alerts` nodes hydrate **fully**: Properties shows every field with a legal
  `_key`, labels honour `labelAttribute`, and an Attribute-based rule authored in the UI
  (`status = closed`, operator dropdown showing `=`) **colours exactly the matching
  nodes**. On large canvases (400+ nodes via broad edge queries) the stub behaviour of
  §1 reproduces. So the hydration failure is size-dependent — likely a cap, timeout, or
  silent failure on the hydration request for large id sets — not a wholesale breakage.
- This also resolves §6's operator question for consumers: string equality is `=` (the
  UI's own operator), and the earlier contradictory observations under `==`/`=` were
  artifacts of evaluating rules against unhydrated stubs, not operator semantics.
- The §5 hazard stands, scoped to the large-canvas regime: the stub Properties panel
  (tell: `_key` containing `/`) still offers Save there.
- Suggested additional diagnostic for §7: bisect the node-count threshold at which
  hydration stops populating attributes, and check whether the hydration POST is absent,
  truncated, or failing at that size.

## 7. Open questions / suggested next diagnostics

1. Browser DevTools → Network while the canvas loads: is the `_api/cursor` POST
   containing `DOCUMENT(` issued at all? If issued, what status/response? (Pending —
   requires an interactive session on the affected deployment.)
2. Has canvas hydration ever worked on this deployment, or did a platform update break
   the database-handle context (`if (!a) …`)?
3. Should the Properties panel be read-only (or Save disabled) whenever the node object
   is a synthesized stub (illegal `_key` is a cheap runtime check)?

## 8. Reproduction (any reader, ~2 minutes)

1. Open `https://prod.demo.pilot.arango.ai/ui/memory/graphs/memory_graph`; run the
   Queries-panel entry "Load: OPEN drift gaps only" (or any edge-returning query).
2. Click any `drift_alerts` node → Properties → observe the 2-field stub and the
   `/`-containing `_key`.
3. Open the same document in Collections UI (or
   `RETURN DOCUMENT("drift_alerts", "<key>")` in the Query editor) → observe full data.
