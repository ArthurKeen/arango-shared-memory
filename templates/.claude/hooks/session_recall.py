#!/usr/bin/env python3
"""SessionStart hook — inject a shared-memory digest into the session context.

Prints (stdout becomes session context): open drift alerts, last sync, PRD
staleness (content hash vs project_registry.prd_sha256), the project's feedback
memories, and the top-ranked relevant memories for this project/type — so recall
happens automatically instead of depending on someone remembering /pattern-search.

Self-configuring: PROJECT_ID / PROJECT_TYPE / PRD_FILE are parsed at runtime from
./AGENTS.md (the consolidated canonical agent doc) with ./CLAUDE.md as the legacy
fallback; credentials resolve env -> arangodb-memory-mcp MCP config -> defaults
(the same three-tier chain every script uses). stdlib only — no python-arango.

FAIL-OPEN BY DESIGN: any error, missing config, or slow network exits 0 silently
within the timeout; a broken hook must never break a session start.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.request

SERVER_ID = "arangodb-memory-mcp"
TIMEOUT = 4  # seconds per HTTP call; the whole hook budget is ~10s


def parse_claude_md(path=None):
    """Extract PROJECT_ID / PROJECT_TYPE / PRD_FILE from the project's agent-doc.

    AGENTS.md (the consolidated canonical doc) is preferred; CLAUDE.md is the
    legacy fallback. Each field is taken from the first file that supplies a
    concrete (non-placeholder) value. An explicit ``path`` reads only that file.
    """
    paths = [path] if path is not None else ["AGENTS.md", "CLAUDE.md"]
    fields = (("project_id", r"PROJECT_ID:\s*([A-Za-z0-9._-]+)"),
              ("project_type", r"PROJECT_TYPE:\s*([A-Za-z0-9._-]+)"),
              ("prd_file", r"PRD_FILE:\s*(\S+)"))
    out = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for key, pat in fields:
            if key in out:
                continue
            m = re.search(pat, text)
            if m and not m.group(1).startswith("<"):
                out[key] = m.group(1)
        if len(out) == len(fields):
            break
    return out


def _from_mcp_config(key):
    for path in ["~/.cursor/mcp.json", "~/.claude.json"]:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                env = json.load(f)["mcpServers"][SERVER_ID]["env"]
            if key in env:
                return env[key]
        except (KeyError, json.JSONDecodeError, OSError):
            continue
    return None


def resolve(key, default=""):
    return os.environ.get(key) or _from_mcp_config(key) or default


def aql(host, db, auth, query, bind_vars=None, insecure=False):
    """One AQL cursor call via the HTTP API. Returns the result list."""
    body = json.dumps({"query": query, "bindVars": bind_vars or {}}).encode()
    req = urllib.request.Request(
        f"{host.rstrip('/')}/_db/{db}/_api/cursor", data=body,
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    ctx = ssl._create_unverified_context() if insecure else None  # noqa: SLF001
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return json.loads(r.read()).get("result", [])


def sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main() -> int:
    cfg = parse_claude_md()
    pid = cfg.get("project_id")
    if not pid:
        return 0  # not a bootstrapped project (or placeholders unrendered)

    host = resolve("ARANGO_HOSTS", "http://localhost:8539").split(",")[0].strip()
    db = resolve("ARANGO_DEFAULT_DB_NAME", "memory")
    user = resolve("ARANGO_ROOT_USERNAME", "root")
    pw = resolve("ARANGO_ROOT_PASSWORD", "")
    insecure = resolve("ARANGO_VERIFY_SSL", "true").lower() in ("0", "false", "no", "off")
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()

    reg = aql(host, db, auth,
              'RETURN DOCUMENT("project_registry", @pid)', {"pid": pid}, insecure)
    reg = reg[0] if reg and reg[0] else {}

    alerts = aql(host, db, auth, """
        FOR d IN drift_alerts
          FILTER d.project_id == @pid AND d.status == "open"
          SORT d.detected_at LIMIT 10
          RETURN {req: d.req_id, gap: d.gap_description}""", {"pid": pid}, insecure)

    feedback = aql(host, db, auth, """
        FOR p IN shared_patterns
          FILTER p.project_id == @pid AND p.memory_type == "feedback"
                 AND p.superseded != true
          SORT p.importance DESC, p.created_at DESC LIMIT 5
          RETURN {desc: p.problem_description, how: p.how_to_apply}""",
                   {"pid": pid}, insecure)

    top = aql(host, db, auth, """
        FOR p IN shared_patterns
          FILTER p.superseded != true
                 AND (p.project_id == @pid OR p.project_type == @ptype)
          LET lu = p.last_used != null ? p.last_used : p.created_at
          LET days = lu == null ? 0 : DATE_DIFF(lu, DATE_NOW(), 'd')
          LET score = ((p.importance == null ? 5 : p.importance) / 10.0)
                      + POW(0.995, days)
                      + LOG(1 + (p.usage_count == null ? 0 : p.usage_count)) / LOG(11)
          SORT score DESC LIMIT 5
          RETURN {key: p._key, cat: p.problem_category, type: p.memory_type,
                  desc: p.problem_description}""",
              {"pid": pid, "ptype": cfg.get("project_type", "other")}, insecure)

    lines = [f"[SHARED-MEMORY] Session digest for project '{pid}':"]
    lines.append(f"  Open drift gaps: {len(alerts)}"
                 + (" — " + ", ".join(a["req"] or "?" for a in alerts) if alerts else "")
                 + f"   Last /prd-sync: {reg.get('last_sync') or 'never'}")

    prd_file = cfg.get("prd_file") or reg.get("prd_path")
    if prd_file:
        current = sha256_file(prd_file)
        stored = reg.get("prd_sha256")
        if current and stored and current != stored:
            lines.append("  ⚠ PRD changed since the last sync — requirements may have "
                         "shifted; run /prd-sync.")
        elif current and not stored:
            lines.append("  PRD baseline not recorded yet — run /prd-sync once to store it.")

    if feedback:
        lines.append("  Feedback memories for this project (follow these):")
        for f in feedback:
            how = f" — apply: {f['how']}" if f.get("how") else ""
            lines.append(f"    - {(f['desc'] or '')[:140]}{how[:160]}")

    if top:
        lines.append("  Top relevant memories (details: /pattern-search <problem>):")
        for t in top:
            tag = f"{t.get('type') or 'pattern'}/{t.get('cat') or '?'}"
            lines.append(f"    - [{tag}] {(t['desc'] or '')[:120]}  ({t['key']})")

    lines.append("  Protocol: /pattern-search before solving -> /pattern-save after -> "
                 "/prd-sync before ending a session that touched implementation files.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - fail open: never break session start
        raise SystemExit(0)
