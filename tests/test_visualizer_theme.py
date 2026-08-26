"""Guards on the Graph Visualizer theme the installer generates.

Stdlib-only and source-level on purpose: scripts/install_visualizer.py imports
python-arango, which CI deliberately does not install (the hook suite must run on a
teammate's machine with no venv). A static check still catches the regression that
matters, because the defect is a literal in the source.

Context these guards encode (see docs/visualizer/BUG-REPORT-node-hydration.md): on the
affected deployment, canvas nodes are frontend-synthesized stubs with no document
attributes, so NO theme rule of any operator can be observed working there — which is
also why two successive "verified" operator claims in this repo turned out to be
unverified. The string-equality wire format remains unknown; it can only be recovered
by authoring a rule through the Visualizer UI on a NON-default theme and reading back
what the UI wrote into _graphThemeStore.
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

    def test_no_string_rule_of_any_operator_is_shipped(self):
        """No string-attribute theme rule ships until its wire format is KNOWN.

        Both formats tried so far produced misleading canvases (observations; the
        mechanisms were never established): "==" rules coincided with every alert
        wearing the base colour; "=" rules coincided with every alert wearing the
        first rule's colour (366 red while only 163 were open). "Known" means: read
        back from _graphThemeStore after authoring the rule through the UI — nothing
        less. This guard covers "==", "=", and any other guess.
        """
        offenders = re.findall(r'_rule\([^)]*"string"[^)]*\)', self.src, re.S)
        self.assertEqual(
            offenders, [],
            "string-attribute theme rule(s) shipped before the operator format was "
            "confirmed from a UI-authored rule:\n" + "\n".join(offenders))

    def test_numeric_rules_are_retained(self):
        """Numeric ">=" rules stay: their FORMAT matches a UI-authored rule.

        Their visual EFFECT is currently unverifiable (stub nodes carry no attributes
        to match), so nothing here may claim they "work" — but the format is correct
        and they become live if node hydration is fixed upstream. Removing them would
        be churn; claiming they work would repeat the day's defining error.
        """
        self.assertIn('">="', self.src)


if __name__ == "__main__":
    unittest.main()
