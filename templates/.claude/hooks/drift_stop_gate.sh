#!/usr/bin/env bash
# Stop hook — BLOCK session end while drift work is queued (not just nudge).
#
# Emits {"decision":"block","reason":...} when .prd-drift-queue/ is non-empty,
# which makes the session continue with the reason as its directive. Rails:
#   - loop-breaker: if stop_hook_active is set in the payload, we already blocked
#     once this chain — always allow the stop (at most ONE block per stop chain).
#   - bypass: a .no-drift-gate file in the repo disables the gate entirely.
#   - fail-open: any parse/listing error allows the stop. A broken gate must
#     never trap a session.

INPUT=$(cat 2>/dev/null || true)

STOP_ACTIVE=$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    print("1" if json.load(sys.stdin).get("stop_hook_active") else "0")
except Exception:
    print("0")' 2>/dev/null || echo 0)
[ "$STOP_ACTIVE" = "1" ] && exit 0

if [ -f .no-drift-gate ]; then
  COUNT=0
else
  COUNT=$(ls .prd-drift-queue 2>/dev/null | wc -l | tr -d ' ')
fi
case "$COUNT" in ''|*[!0-9]*) exit 0;; esac

# `grep -c` prints 0 AND exits 1 on no match; the old `|| echo 0` appended a second
# "0" (the "(including 0\n0 PRD edit(s))" glitch). tr yields a single clean integer.
PRD_COUNT=$(ls .prd-drift-queue 2>/dev/null | grep -c '^prd_' | tr -d ' \n')

APPLY_PENDING=$(printf '%s' "$INPUT" | python3 -c '
import json, os, re, sys
try:
    payload = json.load(sys.stdin)
    raw = payload.get("session_id") or payload.get("conversation_id") or "unknown"
    sid = re.sub(r"[^A-Za-z0-9_.-]", "-", str(raw))[:120]
    with open(os.path.join(".claude", ".shared-memory-sessions", sid + ".json")) as fh:
        state = json.load(fh)
    print(len([k for k in state.get("surfaced_keys", [])
               if k not in set(state.get("applied_keys", []))]))
except Exception:
    print(0)' 2>/dev/null || echo 0)
case "$APPLY_PENDING" in ''|*[!0-9]*) APPLY_PENDING=0;; esac
[ "$COUNT" -eq 0 ] && [ "$APPLY_PENDING" -eq 0 ] && exit 0

python3 - "$COUNT" "$PRD_COUNT" "$APPLY_PENDING" 2>/dev/null <<'PY' || exit 0
import json, sys
count, prd, pending = sys.argv[1], sys.argv[2], sys.argv[3]
extra = f" (including {prd} PRD edit(s))" if prd not in ("", "0") else ""
parts = []
if count != "0":
    parts.append(f"[PRD-DRIFT GATE] {count} change(s) queued since the last /prd-sync{extra}. "
                 "Run /prd-sync now — it audits the changes and clears the queue. "
                 "If a sync is genuinely not wanted for this repository, create a "
                 ".no-drift-gate file to bypass this gate.")
if pending != "0":
    parts.append("[SHARED-MEMORY APPLY GATE] A pattern-search ran but reuse attribution is "
                 "incomplete. Call pattern-applied with only the key(s) actually used; "
                 "if none were used, state that explicitly. Never mark every result as applied.")
print(json.dumps({
    "decision": "block",
    "reason": " ".join(parts),
}))
PY
exit 0
