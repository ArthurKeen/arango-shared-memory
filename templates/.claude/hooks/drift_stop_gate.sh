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
[ -f .no-drift-gate ] && exit 0

COUNT=$(ls .prd-drift-queue 2>/dev/null | wc -l | tr -d ' ')
case "$COUNT" in ''|*[!0-9]*) exit 0;; esac
[ "$COUNT" -eq 0 ] && exit 0

PRD_COUNT=$(ls .prd-drift-queue 2>/dev/null | grep -c '^prd_' 2>/dev/null || echo 0)

python3 - "$COUNT" "$PRD_COUNT" 2>/dev/null <<'PY' || exit 0
import json, sys
count, prd = sys.argv[1], sys.argv[2]
extra = f" (including {prd} PRD edit(s))" if prd not in ("", "0") else ""
print(json.dumps({
    "decision": "block",
    "reason": (f"[PRD-DRIFT GATE] {count} change(s) queued since the last /prd-sync{extra}. "
               "Run /prd-sync now — it audits the changes and clears the queue. "
               "If a sync is genuinely not wanted for this repository, create a "
               ".no-drift-gate file to bypass this gate."),
}))
PY
exit 0
