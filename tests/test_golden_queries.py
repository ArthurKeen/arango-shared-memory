"""Guard: the golden retrieval set must stay well-formed AND discriminating.

The set this replaces was SATURATED: 29 queries, every mode at MRR 0.98, and bm25,
hybrid and hybrid+graph indistinguishable from each other. A benchmark that cannot
separate plain keyword search from the full hybrid+graph pipeline carries no
information, so re-running it against a larger corpus returns the same number and
teaches nothing. It also sat unrun for eight weeks while the corpus grew 75%.

Tier 2 (hard-paraphrase / lexical-trap / hard-concept, added 2026-10-20) exists to
restore the discriminating power. These are structural checks only -- no database, no
API key, stdlib only, like every other guard here. The actual quality numbers come from
`scripts/eval_retrieval.py` against prod; what this file protects is the set's shape,
so tier 2 cannot quietly decay back into tier 1.
"""

import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "eval", "golden_queries.json")

HARD_TIERS = {"hard-paraphrase", "lexical-trap", "hard-concept"}
LEGACY_TIERS = {"paraphrase", "keyword", "concept"}
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _load():
    with open(GOLDEN, encoding="utf-8") as fh:
        return json.load(fh)


class TestGoldenQueries(unittest.TestCase):
    def setUp(self):
        self.doc = _load()
        self.queries = self.doc["queries"]

    def test_every_entry_is_well_formed(self):
        for q in self.queries:
            with self.subTest(key=q.get("_key")):
                self.assertTrue(KEY_RE.match(q["_key"]), "invalid _key for a document")
                self.assertTrue(q["query"].strip(), "empty query")
                self.assertTrue(q["expected"], "no expected keys — unscoreable")
                self.assertIsInstance(q["expected"], list)
                for k in q["expected"]:
                    self.assertTrue(KEY_RE.match(k), f"invalid expected key {k!r}")

    def test_keys_are_unique(self):
        keys = [q["_key"] for q in self.queries]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"duplicate _keys silently overwrite on sync: {dupes}")

    def test_both_tiers_are_present_and_the_hard_tier_is_substantial(self):
        cats = {q["category"] for q in self.queries}
        self.assertTrue(LEGACY_TIERS <= cats, "legacy regression floor was dropped")
        self.assertTrue(HARD_TIERS <= cats, "the discriminating tier is missing")
        hard = [q for q in self.queries if q["category"] in HARD_TIERS]
        self.assertGreaterEqual(
            len(hard), 30,
            "the hard tier must stay large enough for a per-category MRR to mean "
            f"anything; found {len(hard)}")

    def test_no_query_pastes_the_answer_in(self):
        """A query containing its target's own _key (or a project id lifted straight
        from it) is not a retrieval test, it is a lookup. That is how a set becomes
        saturated without anyone deciding to make it easier."""
        for q in self.queries:
            lowered = q["query"].lower()
            for k in q["expected"]:
                with self.subTest(key=q["_key"]):
                    self.assertNotIn(k.lower(), lowered, "query contains the answer key")
                    project = k.split("_")[0]
                    self.assertNotIn(
                        project.lower(), lowered,
                        f"query names the target's project ({project}) — that is a "
                        "lexical giveaway, not a realistic developer query")

    def test_hard_paraphrase_queries_read_as_symptoms_not_titles(self):
        """The hard tier works by withholding the pattern's distinctive vocabulary.
        A one-or-two-word query cannot do that, and a query copied from a pattern
        title defeats the whole point."""
        for q in self.queries:
            if q["category"] not in HARD_TIERS:
                continue
            with self.subTest(key=q["_key"]):
                self.assertGreaterEqual(
                    len(q["query"].split()), 8,
                    "too short to state a symptom without naming the mechanism")

    def test_the_comment_still_explains_how_to_read_the_result(self):
        """The set's value is the SPREAD BETWEEN MODES, not the absolute score. If that
        framing is lost, the next person quotes a headline number again."""
        comment = self.doc["_comment"].lower()
        for phrase in ("saturated", "spread", "bm25", "hybrid+graph"):
            self.assertIn(phrase, comment, f"the _comment no longer explains {phrase!r}")


if __name__ == "__main__":
    unittest.main()
