"""Guard: the eval harness's ranking AQL must stay in sync with the server's.

`scripts/eval_retrieval.py` deliberately COPIES the `pattern-search` ranking AQL so it
can measure the SAME ranking the server serves, without side effects. If the server's
salience formula changes and the eval copy isn't updated, the eval silently measures the
wrong ranking — the exact failure its own docstring warns about. This test fails when the
two diverge.

It locates the server module via ARANGO_MCP_SERVER_DIR or the default
~/code/arango-solutions-mcp-server checkout, and SKIPS when the server repo isn't present
(so this repo's suite still runs standalone / in CI without the server).

Run from the repo root:  python3 -m unittest discover tests
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(REPO, "scripts", "eval_retrieval.py")
SCORE_RE = re.compile(r"LET score = (.+)")


def _server_module():
    for base in (os.environ.get("ARANGO_MCP_SERVER_DIR", ""),
                 os.path.expanduser("~/code/arango-solutions-mcp-server")):
        if base:
            p = os.path.join(base, "mcp_tools", "pattern_memory_tools.py")
            if os.path.isfile(p):
                return p
    return None


def _score_formulas(path):
    with open(path, encoding="utf-8") as fh:
        return {m.strip() for m in SCORE_RE.findall(fh.read())}


class TestEvalAqlSync(unittest.TestCase):
    def setUp(self):
        self.server = _server_module()
        if not self.server:
            self.skipTest("arango-solutions-mcp-server not checked out "
                          "(set ARANGO_MCP_SERVER_DIR to enable this guard)")

    def test_salience_formula_matches_server(self):
        eval_formulas = _score_formulas(EVAL)
        server_formulas = _score_formulas(self.server)
        self.assertTrue(eval_formulas, "no 'LET score =' found in eval_retrieval.py")
        self.assertTrue(server_formulas, "no 'LET score =' found in the server module")
        self.assertEqual(
            eval_formulas, server_formulas,
            "eval_retrieval.py salience formula drifted from the server's pattern-search "
            "ranking; re-copy the AQL (see eval_retrieval.py module docstring).")

    def test_rrf_constants_present_in_both(self):
        for path in (EVAL, self.server):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("1.0/(10+vr+1)", text, f"RRF k-constant missing in {path}")
            self.assertIn("1.0/(10+30)", text, f"graph RRF floor missing in {path}")


if __name__ == "__main__":
    unittest.main()
