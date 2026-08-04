"""DB-free regression tests for the AQL embedded in project-side hooks.

WHY THIS EXISTS (PR #1): two queries in session_recall.py used `desc` as a bare
object-literal attribute name. `DESC` is a reserved AQL keyword, so both were
parse errors (errorNum 1501) — and because the hook is fail-open, the digest
emitted NOTHING, silently, for every project from the day it shipped. The
existing tests passed the whole time: they covered the hook's parsing and its
fail-open guarantee, but never the AQL bodies.

Two independent guards, neither of which needs a database:

  1. reserved-word scan — every AQL string literal in the hooks is checked for
     reserved words used as bare object keys (the exact bug class).
  2. structural scan — braces/parens balance and bind parameters look sane, so a
     truncated or mangled query is caught before it ships.

A parse-level check against a live arangod would be stronger, but these run in
CI with no dependencies and would have caught the shipped defect.

Run from the repo root:  python3 -m unittest discover tests
"""

import ast
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")

# AQL reserved words (3.12). Any of these as a BARE object key is a parse error;
# quoted ("desc":) is legal. Keep alphabetical for easy auditing.
RESERVED = {
    "AGGREGATE", "ALL", "AND", "ANY", "ASC", "COLLECT", "DESC", "DISTINCT",
    "FALSE", "FILTER", "FOR", "GRAPH", "IN", "INBOUND", "INSERT", "INTO",
    "K_SHORTEST_PATHS", "LET", "LIKE", "LIMIT", "NONE", "NOT", "NULL",
    "OPTIONS", "OR", "OUTBOUND", "PRUNE", "REMOVE", "REPLACE", "RETURN",
    "SEARCH", "SHORTEST_PATH", "SORT", "TRUE", "UPDATE", "UPSERT", "WHILE",
    "WINDOW", "WITH",
}

# A string is treated as AQL if it contains an AQL statement keyword.
AQL_HINT = re.compile(r"\b(FOR|RETURN|FILTER|LET|COLLECT|UPSERT|INSERT|UPDATE)\b")

# Bare `key:` inside an object literal — captures the key when NOT quoted.
# Excludes `?`/`:` ternary and `::` by requiring an identifier immediately
# preceded by `{` or `,` (optionally with whitespace/newlines).
BARE_KEY = re.compile(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")


def aql_strings():
    """Yield (filename, string) for every AQL-looking literal in the hooks."""
    for name in sorted(os.listdir(HOOKS)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(HOOKS, name)
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if AQL_HINT.search(node.value):
                    yield name, node.value


class TestNoReservedWordKeys(unittest.TestCase):
    """The PR #1 bug class: reserved word as a bare object-literal key."""

    def test_hook_aql_has_no_bare_reserved_keys(self):
        offenders = []
        for name, query in aql_strings():
            for key in BARE_KEY.findall(query):
                if key.upper() in RESERVED:
                    offenders.append(f"{name}: bare key '{key}:' is a reserved AQL word "
                                     f'— quote it as "{key}":')
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_detector_catches_the_original_defect(self):
        """Guard the guard: the pre-PR-#1 line must be flagged."""
        broken = 'RETURN {desc: p.problem_description, how: p.how_to_apply}'
        found = [k for k in BARE_KEY.findall(broken) if k.upper() in RESERVED]
        self.assertEqual(found, ["desc"])

    def test_detector_accepts_the_fix(self):
        fixed = 'RETURN {"desc": p.problem_description, "how": p.how_to_apply}'
        found = [k for k in BARE_KEY.findall(fixed) if k.upper() in RESERVED]
        self.assertEqual(found, [])

    def test_detector_ignores_non_reserved_bare_keys(self):
        """`req`, `gap`, `cat` etc. are legal bare — no false positives."""
        ok = "RETURN {req: d.req_id, gap: d.gap_description, cat: p.problem_category}"
        found = [k for k in BARE_KEY.findall(ok) if k.upper() in RESERVED]
        self.assertEqual(found, [])


class TestQueryStructure(unittest.TestCase):
    """Cheap structural sanity: catches truncation/mangling before it ships."""

    def test_braces_and_parens_balance(self):
        for name, query in aql_strings():
            self.assertEqual(query.count("{"), query.count("}"),
                             f"{name}: unbalanced braces in AQL")
            self.assertEqual(query.count("("), query.count(")"),
                             f"{name}: unbalanced parens in AQL")

    def test_bind_parameters_are_named(self):
        """Every @param is a valid identifier (@@coll for datasources)."""
        for name, query in aql_strings():
            for param in re.findall(r"@@?([A-Za-z0-9_]*)", query):
                self.assertTrue(param, f"{name}: empty bind parameter name")

    def test_at_least_one_aql_string_found(self):
        """Meta-guard: if the extractor silently finds nothing, these tests are
        vacuous — exactly the failure mode PR #1 exposed."""
        self.assertGreater(len(list(aql_strings())), 0,
                           "no AQL strings extracted — the scan is inert")


if __name__ == "__main__":
    unittest.main()
