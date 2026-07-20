#!/usr/bin/env python3
"""Provision a per-developer, least-privilege user on the shared-memory cluster.

Each teammate gets their OWN user (better attribution + clean offboarding) with
`rw` on the `memory` database ONLY — no access to other databases on the shared
cluster. Generates a strong password and prints the credentials once for you to
hand over out-of-band (never commit them).

Root/admin connection is resolved like the other scripts (env vars →
arangodb-memory-mcp entry in ~/.cursor/mcp.json / ~/.claude.json → defaults), so
after the migration re-point this targets the shared cluster automatically.

Usage:
    poetry run python scripts/add_teammate.py <username>
    poetry run python scripts/add_teammate.py <username> --revoke   # disable/offboard
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

from arango import ArangoClient

SERVER_ID = "arangodb-memory-mcp"
DB = "memory"


def _from_mcp(key):
    for path in ["~/.cursor/mcp.json", "~/.claude.json"]:
        p = os.path.expanduser(path)
        if os.path.exists(p):
            try:
                env = json.load(open(p))["mcpServers"][SERVER_ID]["env"]
                if key in env:
                    return env[key]
            except (KeyError, json.JSONDecodeError, OSError):
                pass
    return None


def resolve(key, default=""):
    return os.environ.get(key) or _from_mcp(key) or default


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.stderr.write("usage: add_teammate.py <username> [--revoke]\n")
        return 2
    username = args[0]
    revoke = "--revoke" in sys.argv

    host = resolve("ARANGO_HOSTS", "http://localhost:8539")
    admin_pw = resolve("ARANGO_ROOT_PASSWORD", "")
    verify = resolve("ARANGO_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
    sysdb = ArangoClient(hosts=host, request_timeout=120, verify_override=verify).db(
        "_system", username=resolve("ARANGO_ROOT_USERNAME", "root"), password=admin_pw)

    if revoke:
        if sysdb.has_user(username):
            sysdb.update_user(username=username, active=False)
            sysdb.update_permission(username=username, permission="none", database=DB)
            print(f"user {username!r}: deactivated + {DB} access revoked")
        else:
            print(f"user {username!r}: not found")
        return 0

    pw = secrets.token_urlsafe(18)  # url-safe: no shell/JSON-hostile chars
    if sysdb.has_user(username):
        sysdb.update_user(username=username, password=pw, active=True)
        action = "password reset"
    else:
        sysdb.create_user(username=username, password=pw, active=True)
        action = "created"

    # On a cluster the _users collection replicates with lag: create_user succeeds but
    # update_permission can 404 ("user not found", ERR 1703) for a few seconds. Retry.
    def grant(perm, database, attempts=30, delay=1.0):
        for i in range(attempts):
            try:
                sysdb.update_permission(username=username, permission=perm, database=database)
                return
            except Exception as exc:  # noqa: BLE001
                if ("1703" in str(exc) or "user not found" in str(exc)) and i < attempts - 1:
                    time.sleep(delay)
                    continue
                raise

    grant("rw", DB)
    try:
        grant("none", "_system", attempts=5)
    except Exception:  # noqa: BLE001
        pass
    print(f"user {username!r}: {action}; granted rw on {DB!r} only")

    # verify least privilege
    probe = ArangoClient(hosts=host, request_timeout=60, verify_override=verify)
    try:
        n = probe.db(DB, username=username, password=pw).collection("shared_patterns").count()
        print(f"  verified: can read {DB} ({n} patterns)")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: could not read {DB} as new user: {str(exc)[:80]}")

    print("\n=== credentials — hand over OUT OF BAND; never commit ===")
    print(f"  ARANGO_HOSTS={host}")
    print(f"  ARANGO_ROOT_USERNAME={username}")
    print(f"  ARANGO_ROOT_PASSWORD={pw}")
    print(f"  ARANGO_DEFAULT_DB_NAME={DB}   ARANGO_VERIFY_SSL=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
