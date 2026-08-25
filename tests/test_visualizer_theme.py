"""Guards on the Graph Visualizer theme the installer generates.

Stdlib-only and source-level on purpose: scripts/install_visualizer.py imports
python-arango, which CI deliberately does not install (the hook suite must run on a
teammate's machine with no venv). A static check still catches the regression that
matters, because the bug is a literal in the source.
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

    def test_no_double_equals_in_theme_rules(self):
        """A "==" in a theme rule is accepted, renders blank, and NEVER matches.

        This shipped: both drift_alert status rules used "==" , so every alert was
        styled by the base colour and closed work was indistinguishable from open
        work on the canvas. The rules were in the database the whole time doing
        nothing. Equality in the Visualizer rule schema is a SINGLE "=".
        """
        offenders = re.findall(r'_rule\([^)]*?"=="[^)]*?\)', self.src, re.S)
        self.assertEqual(
            offenders, [],
            'theme rule(s) use "==" which silently never match; use "=":\n'
            + "\n".join(offenders))

    def test_no_string_equality_rule_is_shipped(self):
        """String equality is unresolved; shipping either candidate misleads.

        "==" never matches (all nodes take the base colour). "=" applies the FIRST
        rule's colour to every node — 366 alerts rendered "open" red when 163 were
        open. The second is worse: it looks like working colour-coding and is read as
        data. Numeric ">=" rules are verified and unaffected.
        """
        offenders = re.findall(r'_rule\([^)]*"string"[^)]*\)', self.src, re.S)
        self.assertEqual(
            offenders, [],
            "string-equality theme rule(s) shipped before the operator is verified "
            "empirically via the Visualizer UI:\n" + "\n".join(offenders))

    def test_comparison_operators_are_left_alone(self):
        """Numeric rules legitimately use >= — the single-= quirk is equality only."""
        self.assertIn('">="', self.src)


if __name__ == "__main__":
    unittest.main()
