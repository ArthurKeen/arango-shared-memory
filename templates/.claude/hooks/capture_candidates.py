#!/usr/bin/env python3
"""Stop hook — mine the session transcript for capture-worthy memories.

Capture is the weakest link of the shared-memory loop: recall is automatic
(SessionStart digest) but saving still depends on someone remembering
/pattern-save. This hook closes half that gap: at session end it scans the
transcript for two cheap, high-signal moments and queues them as CANDIDATES —

  - resolved failures: a tool call (a Bash command, or an MCP tool such as
    execute-aql-query / pattern-search) that errored and a similar later call
    that succeeded (a gotcha was likely solved in between);
  - user corrections: user messages that read like a correction or redirection
    (candidate `feedback` memories — the guidance should outlive this session).

Candidates are NOT saved to shared memory (a dumb hook must not write junk into
a curated corpus). They are queued to .pattern-capture-queue/<session>.json;
the next session's digest surfaces the queue and /pattern-save reviews it with
actual judgment — save the real lessons, delete the noise.

Contract: never blocks, never prints, exits 0 on ANY failure (fail-open, like
every hook in this family). Idempotent per session: re-running overwrites this
session's queue file (and removes it if the session yields no candidates).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

QUEUE_DIR = ".pattern-capture-queue"
MIN_TOOL_CALLS = 5      # skip trivial sessions
MAX_CANDIDATES = 8
SNIPPET = 280           # max chars of evidence kept per candidate

# Correction-ish openers / phrases in a real user message. Deliberately tight:
# false negatives are fine (humans still save via /pattern-save); false
# positives waste reviewer attention.
CORRECTION_RE = re.compile(
    r"(?:^\s*no\b(?!\s+worries)|^\s*nope\b|^\s*wrong\b|\bthat'?s\s+(?:wrong|not\s+right|not\s+what)\b"
    r"|\byou\s+should\s+have\b|\bi\s+meant\b|\binstead\s+of\s+that\b|\bdon'?t\s+do\s+that\b"
    r"|\bstop\s+doing\b|\bthat\s+broke\b|\bstill\s+(?:broken|failing|wrong)\b"
    r"|\bvery\s+weak\b|\bnot\s+acceptable\b)",
    re.IGNORECASE)

ERROR_MARKER_RE = re.compile(
    r"(?:Traceback \(most recent call last\)|command not found|No such file or directory"
    r"|ERR \d{3,4}|\bfatal:|\berror\b[:\s]|\"error\"\s*:\s*\""  # MCP error envelope
    r"|Exception\b|FAILED\b|✗|401|403|500)",
    re.IGNORECASE)


def _last_segment_head(command):
    """First token of the last pipeline/&&-segment — the program that actually ran.

    'cd ~/x && pytest -q' -> 'pytest'; 'a | b; c' -> 'c'. Good enough to say a
    later success is "the same kind of command" as an earlier failure.
    """
    segments = [s.strip() for s in re.split(r"&&|\|\||[;|]", command or "") if s.strip()]
    if not segments:
        return None
    head = segments[-1].split()
    return head[0] if head else None


def _text_of(content):
    """Flatten a tool_result / message content field to text (defensively)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _clean(text, limit=SNIPPET):
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _event_identity(name, command):
    """A stable key + human label for a tool call, or (None, None) to skip.

    Bash is keyed by the program that actually ran; MCP tools (hyphenated names
    like execute-aql-query, pattern-search) by their tool name. Edit/Write/Read
    and other benign-churn tools are skipped — a fail→success there rarely means
    a gotcha was solved, and false positives waste reviewer attention.
    """
    if name == "Bash":
        return _last_segment_head(command), _clean(command, 160)
    if name and "-" in name:   # MCP tool
        return name, name
    return None, None


def mine(transcript_path):
    """Return (candidates, tool_call_count) mined from a Claude Code JSONL transcript."""
    tool_use = {}          # id -> {"name","command"}
    tool_events = []       # ordered: {"key","label","ok"} across Bash + MCP tools
    corrections = []

    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            msg = entry.get("message") or {}
            content = msg.get("content")
            etype = entry.get("type")

            if etype == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_use[block.get("id")] = {
                            "name": block.get("name"),
                            "command": (block.get("input") or {}).get("command", "")}

            elif etype == "user":
                if isinstance(content, list):
                    plain = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            tu = tool_use.get(block.get("tool_use_id"))
                            if not tu:
                                continue
                            key, label = _event_identity(tu.get("name"), tu.get("command"))
                            if not key:
                                continue
                            out = _text_of(block.get("content"))
                            failed = bool(block.get("is_error")) or bool(
                                ERROR_MARKER_RE.search(out[:2000]))
                            tool_events.append({"key": key, "label": label, "ok": not failed})
                        elif block.get("type") == "text":
                            plain.append(block.get("text") or "")
                    text = "\n".join(plain)
                elif isinstance(content, str):
                    text = content
                else:
                    text = ""
                # skip harness/meta pseudo-messages; keep genuine typed input
                if text and not text.startswith(("<system-reminder", "<local-command",
                                                 "<command-name", "[SYSTEM")):
                    if CORRECTION_RE.search(text[:400]):
                        corrections.append(_clean(text))

    # resolved failures: a tool (by key) failed, then the same key later succeeded
    candidates, seen = [], set()
    for i, ev in enumerate(tool_events):
        if ev["ok"] or not ev["key"] or ev["key"] in seen:
            continue
        for later in tool_events[i + 1:]:
            if later["key"] == ev["key"] and later["ok"]:
                seen.add(ev["key"])
                candidates.append({
                    "kind": "resolved-failure",
                    "summary": f"'{ev['key']}' failed and later succeeded — "
                               "a gotcha was likely solved in between",
                    "evidence": {"failed": ev["label"], "succeeded": later["label"]}})
                break

    for c in corrections[:4]:
        candidates.append({"kind": "user-correction",
                           "summary": "user correction/redirection — candidate feedback memory",
                           "evidence": {"message": c}})

    return candidates[:MAX_CANDIDATES], len(tool_events)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    transcript = payload.get("transcript_path") or ""
    if not transcript or not os.path.isfile(transcript):
        return 0
    session = payload.get("session_id") or os.path.splitext(os.path.basename(transcript))[0]
    session = re.sub(r"[^A-Za-z0-9_-]", "-", str(session))[:80]
    queue_file = os.path.join(QUEUE_DIR, f"{session}.json")

    candidates, tool_calls = mine(transcript)

    if not candidates or tool_calls < MIN_TOOL_CALLS:
        # idempotency: a re-run that finds nothing clears this session's stale file
        try:
            os.remove(queue_file)
        except OSError:
            pass
        return 0

    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(queue_file, "w", encoding="utf-8") as fh:
        json.dump({"session_id": session,
                   "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "note": "Candidate memories mined from the session transcript. "
                           "Review with /pattern-save: save the real lessons "
                           "(pattern/feedback), then delete this file.",
                   "candidates": candidates}, fh, indent=2)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — fail open: never block or break a stop
        raise SystemExit(0)
