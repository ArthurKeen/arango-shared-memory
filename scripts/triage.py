#!/usr/bin/env python3
"""Triage the three human-review queues — and make them drainable.

Nothing in this system auto-applies: proposed PRD patches, sync observations and
drift alerts all wait for a human. That is the right policy and it is not the
problem. The problem is that nothing drains them, so the policy quietly turns into
a landfill:

    2026-08-28   patches 12   observations  68   open alerts 152
    2026-09-28   patches 26   observations  88   open alerts 172
    2026-10-21   patches 38   observations 153   open alerts 200

Growing in every queue at every reading. And the growth is not evenly spread — most
of it sits in a handful of projects, and more than half the open alerts predate
September. An alert nobody has looked at in two months is not a work item, it is
noise in the SessionStart digest, and noise is what makes the other 90 get ignored.

This script answers the two questions needed to either work the queues or cap them
on purpose:

  WHERE is the backlog?       per-project counts, so effort can be aimed
  WHAT is probably dead?      age buckets, plus three mechanical staleness signals:
                                - contradicted: the same project+req_id ALSO has a
                                  closed alert, so the gap was fixed and the open
                                  row was never cleared
                                - orphaned: the project is not in project_registry
                                  at all (renamed, archived, or never registered)
                                - ancient: older than --stale-days (default 60) with
                                  no closing evidence

READ-ONLY BY DEFAULT. `--age-out` is the "cap them on purpose" path: it moves
ancient open alerts to status 'stale' — NOT deleted, NOT closed. A close asserts
the gap was fixed, which nobody has verified; 'stale' asserts only that nobody has
looked in a long time, which is true and provable from the timestamps. Every aged
row records aged_out_at / aged_out_reason / aged_out_by and keeps its original
status in prior_status, so one AQL UPDATE reverses the whole batch.

Writes touch shared prod data that four people now read, so --age-out additionally
requires --yes and prints the exact reversal query before it runs.

Usage (via the MCP server's venv, which has python-arango):
    cd ~/code/arango-solutions-mcp-server
    .venv/bin/python ~/code/arango-shared-memory/scripts/triage.py
Options:
    --stale-days N   age threshold for 'ancient' (default 60)
    --project P      restrict the report to one project
    --age-out        propose the stale transition (still a dry run without --yes)
    --yes            actually write the stale transition
Exit: 0 ok · 1 failure/misconfiguration · 2 python-arango missing.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from arango import ArangoClient
except ModuleNotFoundError:
    sys.stderr.write("error: python-arango missing — run via the MCP server's venv:\n"
                     "  cd ~/code/arango-solutions-mcp-server && .venv/bin/python "
                     "~/code/arango-shared-memory/scripts/triage.py\n")
    sys.exit(2)

SERVER_ID = "arangodb-memory-mcp"


def _arg(flag: str, default: str = "") -> str:
    """--flag value, tolerating --flag=value."""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


STALE_DAYS = int(_arg("--stale-days", "60"))
ONLY_PROJECT = _arg("--project")
AGE_OUT = "--age-out" in sys.argv
CONFIRMED = "--yes" in sys.argv


# ---------------------------------------------------------------- connection
def _from_mcp_config(key: str):
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


def resolve(key: str, default: str = "") -> str:
    return os.environ.get(key) or _from_mcp_config(key) or default


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    hosts = resolve("ARANGO_HOSTS", "http://localhost:8529")
    db_name = resolve("ARANGO_DEFAULT_DB_NAME", "memory")
    user = resolve("ARANGO_ROOT_USERNAME", "root")
    pw = resolve("ARANGO_ROOT_PASSWORD", "")
    client = ArangoClient(hosts=hosts.split(",")[0].strip())
    return client.db(db_name, username=user, password=pw, verify=True), hosts, db_name, user


# --------------------------------------------------------------------- AQL
# Age buckets are computed in the database so a 200-row scan stays one round trip.
QUEUES_AQL = """
LET cutoff = @cutoff
LET alerts = (
  FOR a IN drift_alerts FILTER a.status == "open"
    FILTER @proj == "" OR a.project_id == @proj
    COLLECT project = a.project_id INTO rows
    LET ancient = LENGTH(rows[* FILTER CURRENT.a.detected_at < cutoff])
    RETURN { project, open: LENGTH(rows), ancient })
LET patches = (
  FOR p IN prd_patches FILTER p.review_state == "proposed"
    FILTER @proj == "" OR p.project_id == @proj
    COLLECT project = p.project_id INTO rows
    LET ancient = LENGTH(rows[* FILTER CURRENT.p.created_at < cutoff])
    RETURN { project, open: LENGTH(rows), ancient })
LET obs = (
  FOR o IN sync_observations FILTER o.state == "unprocessed"
    FILTER @proj == "" OR o.project_id == @proj
    COLLECT project = o.project_id INTO rows
    LET ancient = LENGTH(rows[* FILTER CURRENT.o.created_at < cutoff])
    RETURN { project, open: LENGTH(rows), ancient })
RETURN { alerts, patches, obs }
"""

# An open alert whose project+req_id ALSO has a closed row: the gap was fixed under a
# different alert document and this one was never cleared. Mechanically resolvable.
CONTRADICTED_AQL = """
LET closed = (FOR c IN drift_alerts FILTER c.status == "closed"
              RETURN CONCAT(c.project_id, "/", c.req_id))
FOR a IN drift_alerts FILTER a.status == "open"
  FILTER @proj == "" OR a.project_id == @proj
  FILTER CONCAT(a.project_id, "/", a.req_id) IN closed
  RETURN { _key: a._key, project: a.project_id, req: a.req_id, detected: a.detected_at }
"""

# An open alert for a project that is not registered: renamed, archived, or it never
# ran /prd-sync again. Nobody owns these.
ORPHANED_AQL = """
LET known = (FOR r IN project_registry RETURN r.project_id)
FOR a IN drift_alerts FILTER a.status == "open"
  FILTER @proj == "" OR a.project_id == @proj
  FILTER a.project_id NOT IN known
  COLLECT project = a.project_id WITH COUNT INTO n
  SORT n DESC RETURN { project, n }
"""

ANCIENT_AQL = """
FOR a IN drift_alerts FILTER a.status == "open"
  FILTER @proj == "" OR a.project_id == @proj
  FILTER a.detected_at < @cutoff
  FILTER a.closed_evidence == null OR a.closed_evidence == ""
  RETURN a._key
"""

AGE_OUT_AQL = """
FOR a IN drift_alerts FILTER a._key IN @keys
  UPDATE a WITH { status: "stale", prior_status: a.status,
                  aged_out_at: @now, aged_out_by: @by,
                  aged_out_reason: @reason } IN drift_alerts
  RETURN NEW._key
"""

REVERSAL = ("FOR a IN drift_alerts FILTER a.aged_out_at == \"{now}\" "
            "UPDATE a WITH {{ status: a.prior_status, aged_out_at: null, "
            "aged_out_by: null, aged_out_reason: null, prior_status: null }} "
            "IN drift_alerts")


def _table(title, rows, total_label):
    print(f"\n  {title}")
    if not rows:
        print("    (empty)")
        return
    rows = sorted(rows, key=lambda r: (-r["open"], r["project"]))
    print(f"    {'project':<38} {'open':>5} {'>' + str(STALE_DAYS) + 'd':>6}")
    for r in rows[:12]:
        print(f"    {r['project'][:38]:<38} {r['open']:>5} {r['ancient']:>6}")
    if len(rows) > 12:
        print(f"    … {len(rows) - 12} more project(s)")
    print(f"    {total_label}: {sum(r['open'] for r in rows)} "
          f"across {len(rows)} project(s), "
          f"{sum(r['ancient'] for r in rows)} older than {STALE_DAYS} days")


def main() -> int:
    try:
        db, hosts, db_name, user = connect()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: cannot connect: {exc}\n")
        return 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    binds = {"cutoff": cutoff, "proj": ONLY_PROJECT}

    print(f"Triage — {hosts} db={db_name!r} user={user!r}  "
          f"stale threshold {STALE_DAYS}d (before {cutoff[:10]})"
          + (f"  project={ONLY_PROJECT!r}" if ONLY_PROJECT else ""))

    q = next(iter(db.aql.execute(QUEUES_AQL, bind_vars=binds)))
    _table("open drift alerts", q["alerts"], "total")
    _table("proposed PRD patches", q["patches"], "total")
    _table("unprocessed sync observations", q["obs"], "total")

    contradicted = list(db.aql.execute(CONTRADICTED_AQL, bind_vars={"proj": ONLY_PROJECT}))
    orphaned = list(db.aql.execute(ORPHANED_AQL, bind_vars={"proj": ONLY_PROJECT}))
    ancient = list(db.aql.execute(ANCIENT_AQL, bind_vars=binds))

    print("\n  mechanically resolvable (no judgement required)")
    print(f"    contradicted : {len(contradicted):>4}  open alert whose project+req_id "
          "also has a CLOSED row — the gap was fixed, the row was never cleared")
    for c in contradicted[:8]:
        print(f"                   - {c['project']}/{c['req']}  ({c['detected'][:10]})")
    if len(contradicted) > 8:
        print(f"                     … {len(contradicted) - 8} more")
    print(f"    orphaned     : {sum(o['n'] for o in orphaned):>4}  open alert(s) whose "
          "project is not in project_registry")
    for o in orphaned[:8]:
        print(f"                   - {o['project']}: {o['n']}")
    print(f"    ancient      : {len(ancient):>4}  open >{STALE_DAYS}d with no closing "
          "evidence")

    if not AGE_OUT:
        print("\n  Nothing was written. To cap the alert queue on purpose, re-run with "
              "--age-out (and --yes to commit).")
        return 0

    if not ancient:
        print("\n  --age-out: nothing older than the threshold. Nothing to do.")
        return 0

    reason = f"no review in {STALE_DAYS}+ days; aged out by scripts/triage.py"
    print(f"\n  --age-out: {len(ancient)} open alert(s) -> status 'stale'")
    print("    'stale' is NOT 'closed'. Closing asserts the gap was fixed, which nobody "
          "has verified.\n    Stale asserts only that nobody has looked in "
          f"{STALE_DAYS}+ days, which the timestamps prove.")
    if not CONFIRMED:
        print("\n  DRY RUN — add --yes to commit. Sample of what would change:")
        for k in ancient[:10]:
            print(f"    {k}")
        if len(ancient) > 10:
            print(f"    … {len(ancient) - 10} more")
        return 0

    now = now_iso()
    print(f"\n  REVERSAL (copy this before continuing):\n    {REVERSAL.format(now=now)}\n")
    updated = list(db.aql.execute(AGE_OUT_AQL, bind_vars={
        "keys": ancient, "now": now, "by": user, "reason": reason}))
    print(f"  aged out {len(updated)} alert(s) at {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
