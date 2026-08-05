#!/usr/bin/env python3
"""Mechanical evidence verifier for /prd-sync (Phase 2.5).

Reads a claims JSON document on stdin and verifies every file:line citation
against the working tree, so an IMPLEMENTED classification can never rest on a
citation that does not exist. This is the non-overridable gate between the
audit and the database: a claim the script cannot confirm must be downgraded.

stdin:
    {"claims": [
        {"req_id": "REQ-001", "classification": "IMPLEMENTED",
         "evidence": ["src/auth/jwt.ts:42", "src/auth/mw.ts:10-25"],
         "term": "jwt"}          # optional: a term that must appear in the file
    ]}

stdout: JSON verdicts per claim; exit 0 = all IMPLEMENTED/OUTDATED-PRD claims
verified, exit 1 = at least one failed (downgrade those to PARTIAL), exit 2 =
malformed input.

Evidence verdicts: verified | missing_file | line_out_of_range | term_not_found
| malformed. A claim is verified only if it has >=1 evidence item and all items
verify. Classifications that assert code exists (IMPLEMENTED, OUTDATED-PRD,
TEST-ONLY, PARTIAL-with-evidence) are checked; MISSING/SKIP pass through.
"""

from __future__ import annotations

import json
import os
import re
import sys

# Classifications whose evidence must verify for the claim to stand.
CHECKED = {"IMPLEMENTED", "OUTDATED-PRD", "TEST-ONLY"}
_LINE_REF = re.compile(r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$")
# Lines of slack around a cited range when checking that `term` appears there.
# A cited term must be NEAR the cited line, not merely somewhere in the file —
# otherwise the citation could point anywhere and still "verify".
TERM_CONTEXT = 3


def parse_evidence(item: str):
    """Split 'path:NN' / 'path:NN-MM' / bare 'path' → (path, start, end).

    start/end are None for a bare path. Returns None for a non-string item.
    """
    if not isinstance(item, str) or not item.strip():
        return None
    item = item.strip()
    m = _LINE_REF.match(item)
    if m:
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        return m.group("path"), start, end
    return item, None, None


def check_evidence_item(item: str, term: str | None = None, root: str = "."):
    """Verify one citation. Returns (verdict, detail)."""
    parsed = parse_evidence(item)
    if parsed is None:
        return "malformed", "evidence item is not a non-empty string"
    path, start, end = parsed
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return "missing_file", f"{path} does not exist"
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return "missing_file", f"{path}: {exc}"
    if start is not None:
        if start < 1 or start > len(lines) or end < start:
            return "line_out_of_range", f"{path} has {len(lines)} lines, cited {start}" + (
                f"-{end}" if end != start else "")
    if term:
        # Line-local when a line is cited (term must be near it); whole-file only
        # for a bare path with no line number.
        if start is not None:
            lo = max(0, start - 1 - TERM_CONTEXT)
            hi = min(len(lines), end + TERM_CONTEXT)
            blob = "".join(lines[lo:hi]).lower()
            where = f"near line {start}" + (f"-{end}" if end != start else "")
        else:
            blob = "".join(lines).lower()
            where = f"in {path}"
        if term.lower() not in blob:
            return "term_not_found", f"{term!r} not found {where}"
    return "verified", ""


def check_claim(claim: dict, root: str = "."):
    """Verify one claim. Returns a result dict with per-evidence verdicts."""
    req_id = claim.get("req_id", "?")
    classification = str(claim.get("classification", "")).upper()
    evidence = claim.get("evidence") or []
    term = claim.get("term")

    result = {"req_id": req_id, "classification": classification,
              "evidence_results": [], "verdict": "verified", "reason": ""}

    if classification not in CHECKED:
        # PARTIAL: verify citations if present, but absence of evidence is legal.
        if classification == "PARTIAL" and evidence:
            pass  # fall through to the evidence loop below
        else:
            result["verdict"] = "not_checked"
            return result

    if classification in CHECKED and not evidence:
        result["verdict"] = "failed"
        result["reason"] = "no evidence cited for a classification that asserts code exists"
        return result

    for item in evidence:
        verdict, detail = check_evidence_item(item, term=term, root=root)
        result["evidence_results"].append(
            {"evidence": item, "verdict": verdict, "detail": detail})
        if verdict != "verified":
            result["verdict"] = "failed"
            result["reason"] = f"{item}: {verdict}" + (f" ({detail})" if detail else "")
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        claims = payload["claims"]
        assert isinstance(claims, list)
    except Exception as exc:  # noqa: BLE001 - report any malformed input uniformly
        json.dump({"error": f"malformed input: {exc}"}, sys.stdout, indent=2)
        print()
        return 2

    root = payload.get("root", ".")
    results = [check_claim(c, root=root) for c in claims]
    failed = [r for r in results if r["verdict"] == "failed"]
    json.dump({
        "results": results,
        "summary": {
            "claims": len(results),
            "verified": sum(1 for r in results if r["verdict"] == "verified"),
            "failed": len(failed),
            "action_required": ("downgrade the failed IMPLEMENTED/OUTDATED-PRD/TEST-ONLY "
                                "claims to PARTIAL (gap: evidence unverifiable)" if failed else ""),
        },
    }, sys.stdout, indent=2)
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
