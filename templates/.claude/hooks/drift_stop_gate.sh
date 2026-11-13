#!/usr/bin/env bash
# Stop hook — BLOCK session end while drift work is queued (not just nudge).
#
# Emits {"decision":"block","reason":...} when .prd-drift-queue/ is non-empty,
# which makes the session continue with the reason as its directive. Rails:
#   - loop-breaker: if stop_hook_active is set in the payload, we already blocked
#     once this chain — always allow the stop (at most ONE block per stop chain).
#   - bypass: a .no-drift-gate file in the repo disables the gate entirely. The main
#     checkout's file also covers its git worktrees, which never have their own copy
#     (it is gitignored).
#   - fail-open: any parse/listing error allows the stop. A broken gate must
#     never trap a session.

INPUT=$(cat 2>/dev/null || true)

# The checkout these hooks are installed in (<root>/.claude/hooks/<this file>). In a git
# worktree the cwd is the worktree, which has no .claude/hooks of its own, so sibling
# scripts are run from here, never by a cwd-relative path. Empty on failure, which makes
# the reconciler call below fail, and that fails open like every other step.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." 2>/dev/null && pwd)

STOP_ACTIVE=$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    print("1" if json.load(sys.stdin).get("stop_hook_active") else "0")
except Exception:
    print("0")' 2>/dev/null || echo 0)
[ "$STOP_ACTIVE" = "1" ] && exit 0

if [ -f .no-drift-gate ] || { [ -n "$ROOT" ] && [ -f "$ROOT/.no-drift-gate" ]; }; then
  COUNT=0
else
  # Recover edits the PostToolUse hook could not see before counting. It fires on
  # Write|Edit and reads tool_input.file_path; a Bash heredoc, `sed -i`, a generated
  # script or an external editor carries none, so those edits queue nothing and this
  # gate would pass a session with unaudited changes. The reconciler asks git instead.
  # Fails open (not a git repo, git missing, any error -> queues nothing), so a
  # failure here degrades to the old behaviour rather than trapping the session.
  # stdout must stay pure JSON or the client cannot parse the block decision.
  # >&2 keeps the reconciler's notice AND any traceback visible; >/dev/null 2>&1
  # would silence both, and a hook that hides its own crashes is what caused
  # three multi-day outages here.
  python3 "$ROOT/.claude/hooks/reconcile_drift_queue.py" >&2 || true
  COUNT=$(ls .prd-drift-queue 2>/dev/null | wc -l | tr -d ' ')
fi
case "$COUNT" in ''|*[!0-9]*) exit 0;; esac

# `grep -c` prints 0 AND exits 1 on no match; the old `|| echo 0` appended a second
# "0" (the "(including 0\n0 PRD edit(s))" glitch). tr yields a single clean integer.
PRD_COUNT=$(ls .prd-drift-queue 2>/dev/null | grep -c '^prd_' | tr -d ' \n')

# Emits "<pending> <session-id> <comma-joined pending keys>" so the block message can
# name the exact command that resolves it. Fails open to "0 - -" like everything else.
APPLY_INFO=$(printf '%s' "$INPUT" | python3 -c '
import json, os, re, sys
try:
    payload = json.load(sys.stdin)
    raw = payload.get("session_id") or payload.get("conversation_id") or "unknown"
    sid = re.sub(r"[^A-Za-z0-9_.-]", "-", str(raw))[:120]
    # Resolve the state directory the way the WRITERS do, or the gate reads a
    # different file than the trackers wrote. Addressing it relative to the cwd split
    # a session that moved into a git worktree: surfaced keys in one file, applied
    # keys in the other, and this gate then either blocked forever on keys that had
    # been applied or let the session end with keys unresolved (r2g, 2026-11-12).
    # Importing hook_paths keeps the one definition shared; if it is missing (an old
    # install), fall back to the previous cwd-relative behaviour rather than failing.
    # ROOT comes in as argv[1]: __file__ does not exist under `python3 -c`, so
    # deriving the hooks directory here would silently take the fallback every time.
    sys.path.insert(0, os.path.join(sys.argv[1] if len(sys.argv) > 1 else ".",
                                    ".claude", "hooks"))
    try:
        from hook_paths import merge_legacy_state, state_dir
        merge_legacy_state(sid)
        state_path = os.path.join(state_dir(), sid + ".json")
    except Exception:
        state_path = os.path.join(".claude", ".shared-memory-sessions", sid + ".json")
    with open(state_path) as fh:
        state = json.load(fh)
    # A surfaced key is RESOLVED when it was either applied (reuse attributed) or
    # explicitly dismissed (reviewed, deliberately not reused). Counting only
    # applied_keys made this gate unsatisfiable: it demanded that every surfaced key
    # be applied while its own message forbids marking every result as applied. The
    # third state is recorded by .claude/hooks/dismiss_surfaced.py — never by
    # inventing apply events, which would inflate usage_count and corrupt the
    # learned success-rate ranking for everyone else.
    resolved = set(state.get("applied_keys", [])) | set(state.get("dismissed_keys", []))
    pending = [k for k in state.get("surfaced_keys", []) if k not in resolved]
    print(len(pending), sid, ",".join(pending) or "-")
except Exception:
    print("0 - -")' "$ROOT" 2>/dev/null || echo "0 - -")
APPLY_PENDING=$(printf '%s' "$APPLY_INFO" | cut -d' ' -f1)
APPLY_SID=$(printf '%s' "$APPLY_INFO" | cut -d' ' -f2)
APPLY_KEYS=$(printf '%s' "$APPLY_INFO" | cut -d' ' -f3)
case "$APPLY_PENDING" in ''|*[!0-9]*) APPLY_PENDING=0;; esac
[ "$COUNT" -eq 0 ] && [ "$APPLY_PENDING" -eq 0 ] && exit 0

python3 - "$COUNT" "$PRD_COUNT" "$APPLY_PENDING" "$APPLY_SID" "$APPLY_KEYS" "$ROOT" 2>/dev/null <<'PY' || exit 0
import json, os, shlex, sys
count, prd, pending, sid, keys, root = (sys.argv[1:] + [""] * 6)[:6]
# Absolute, so the command also runs from a git worktree, which has no .claude/hooks.
dismiss = (shlex.quote(os.path.join(root, ".claude/hooks/dismiss_surfaced.py")) if root
           else ".claude/hooks/dismiss_surfaced.py")
extra = f" (including {prd} PRD edit(s))" if prd not in ("", "0") else ""
parts = []
if count != "0":
    parts.append(f"[PRD-DRIFT GATE] {count} change(s) queued since the last /prd-sync{extra}. "
                 "Run /prd-sync now — it audits the changes and clears the queue. "
                 "If a sync is genuinely not wanted for this repository, create a "
                 ".no-drift-gate file to bypass this gate.")
if pending != "0":
    # Both halves must be actionable: prose ("state that explicitly") has no effect on
    # the state file, so a message that only says that leaves the gate unsatisfiable.
    spaced = keys.replace(",", " ") if keys not in ("", "-") else ""
    parts.append(f"[SHARED-MEMORY APPLY GATE] {pending} surfaced pattern(s) unresolved. "
                 "THREE outcomes, not two. (1) It informed the solution: pattern-applied "
                 "with only those key(s), outcome='worked'. (2) You TRIED it and it did not "
                 "help — the checks it should have made pass are still failing, you reverted "
                 "it for another approach, or it introduced a defect: pattern-applied with "
                 "outcome='failed'. That is a useful signal, not a confession, and it is the "
                 "only way a pattern's ranking can ever fall. (3) Reviewed and never tried: "
                 f"python3 {dismiss} {sid} {spaced} "
                 "Do not use (3) for something you actually tried — that discards the "
                 "evidence. Never mark every result as applied.")
print(json.dumps({
    "decision": "block",
    "reason": " ".join(parts),
}))
PY
exit 0
