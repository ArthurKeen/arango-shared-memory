"""Guards on the Graph Visualizer theme the installer generates.

Stdlib-only and source-level on purpose: scripts/install_visualizer.py imports
python-arango, which CI deliberately does not install (the hook suite must run on a
teammate's machine with no venv). A static check still catches the regression that
matters, because the defect is a literal in the source.

Operator facts these guards encode (docs/visualizer/BUG-REPORT-node-hydration.md):
- String equality is a single "=" — RESOLVED 2026-08-26 by direct observation: a rule
  authored through the Visualizer's own Attribute-based editor (whose operator dropdown
  displays "=") visibly coloured exactly the matching nodes on a hydrated canvas.
- "==" is accepted by the store but is not what the UI writes; the one time it shipped,
  no styling was ever observed from it. It stays banned.
- Rules only render on canvases whose nodes are hydrated. On LARGE canvases the
  visualizer leaves nodes as attribute-less stubs (size-dependent product bug), where
  no rule of any operator can match — that regime produced this repo's earlier false
  operator "verifications", and no guard here can test rendering; only formats.
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, "scripts", "install_visualizer.py")


class TestThemeRuleOperators(unittest.TestCase):
    def setUp(self):
        with open(INSTALLER, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_string_rules_use_single_equals_only(self):
        """Every string-attribute rule must use "=" — the UI's own operator.

        A "==" rule is stored without error and never styles anything, so the defect
        is invisible until someone stares at an unchanged canvas. This shipped once.
        """
        rules = re.findall(r'_rule\([^)]*"string"[^)]*\)', self.src, re.S)
        self.assertGreater(len(rules), 0, "expected string status rules to be shipped")
        offenders = [r for r in rules if '"="' not in r]
        self.assertEqual(
            offenders, [],
            'string rule(s) not using the UI-verified "=" operator:\n'
            + "\n".join(offenders))

    def test_double_equals_never_reappears(self):
        """The "==" operator must not appear in any theme rule, string or numeric."""
        offenders = re.findall(r'_rule\([^)]*"=="[^)]*\)', self.src, re.S)
        self.assertEqual(offenders, [], '"==" theme rule(s) reintroduced:\n' + "\n".join(offenders))

    def test_every_observed_status_value_has_a_rule(self):
        """The live census values must each be styled (first-match-wins ordering).

        drift_alerts.status: open/undocumented/closed; prd_patches.review_state:
        proposed/accepted (+ rejected/superseded for the future); sync_observations
        .state: unprocessed/promoted/acknowledged/duplicate.
        """
        for value in ("open", "undocumented", "closed",
                      "proposed", "accepted",
                      "unprocessed", "promoted", "acknowledged"):
            self.assertIn(f'"{value}"', self.src,
                          f"no theme rule for observed status value {value!r}")

    def test_numeric_rules_are_retained(self):
        """Numeric ">=" rules stay — format matches a UI-authored rule."""
        self.assertIn('">="', self.src)


if __name__ == "__main__":
    unittest.main()
