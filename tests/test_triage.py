"""Guard: triage.py must never age out alerts belonging to a LIVE project.

The first version of `--age-out` asked one question -- how old is this alert -- and
proposed sweeping 97 rows. That question is wrong, and the counter-example was sitting
in the fleet at the time:

  arangodb-data-tools-rs went 27 commits in June, 11 in July, 5 in August, then 39 days
  of total silence, then 18 commits in October, a v0.1.0 tag on 2026-10-19, and a
  /prd-sync on 2026-10-21. 35 of its 36 open alerts were detected in August, during the
  quiet stretch, so every one of them looked ancient. They are live tracked work against
  130 requirements in the most active project in the fleet.

A sample taken in September would have looked terminal and been wrong. Dormancy is a
property of the PROJECT, not of the timestamp on a row. Adding the project-activity gate
took the sweep from 97 to 53 and protected 45 rows across 4 still-syncing projects.

Source-level and stdlib-only like the other guards here: CI installs no dependencies and
has no database.
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIAGE = os.path.join(REPO, "scripts", "triage.py")


def _read():
    with open(TRIAGE, encoding="utf-8") as fh:
        return fh.read()


def _block(src, name):
    """The AQL string assigned to <name>."""
    m = re.search(rf'^{name}\s*=\s*"""(.*?)"""', src, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


class TestTriageAgeOutSafety(unittest.TestCase):
    def setUp(self):
        self.src = _read()
        self.ancient = _block(self.src, "ANCIENT_AQL")

    def test_the_guard_can_find_the_query(self):
        """Guard the guard: a regex matching nothing would pass every check below."""
        self.assertTrue(self.ancient, "ANCIENT_AQL not found — this guard is inert")
        self.assertIn("drift_alerts", self.ancient)

    def test_ancient_requires_the_project_to_be_dormant_too(self):
        """Alert age alone is not sufficient grounds to sweep a row."""
        self.assertIn("project_registry", self.ancient,
                      "ANCIENT_AQL does not consult project_registry — it is deciding "
                      "dormancy from alert age alone, which swept 45 live rows")
        self.assertIn("last_sync", self.ancient,
                      "ANCIENT_AQL does not look at last_sync, the only field that says "
                      "whether anyone is still working the project")
        self.assertRegex(
            self.ancient, r"a\.project_id IN \w+",
            "ANCIENT_AQL does not restrict alerts to the dormant project set")

    def test_registry_timestamps_are_compared_by_date_prefix(self):
        """Registry timestamps carry mixed forms -- "2026-10-21T00:00:00Z" next to
        "2026-10-01T19:24:00-06:00". A whole-string compare across offsets is
        lexicographically wrong; the date prefix is correct to the day, which is ample
        for a 60-day threshold."""
        self.assertIn("LEFT(r.last_sync, 10)", self.ancient,
                      "last_sync compared as a whole string — wrong across UTC offsets")
        self.assertIn("LEFT(a.detected_at, 10)", self.ancient,
                      "detected_at compared as a whole string")

    def test_a_project_that_never_synced_still_counts_as_dormant(self):
        """A null last_sync must not silently exclude a project from the sweep."""
        self.assertIn("r.last_sync == null", self.ancient,
                      "a project with no recorded sync would be treated as active")

    def test_revived_alerts_are_reported_rather_than_silently_dropped(self):
        """The rows the old version would have swept are the interesting ones. Filtering
        them out without saying so would hide the very case that motivated the fix."""
        self.assertIn("REVIVED_AQL", self.src,
                      "no reporting of old-alerts-in-an-active-project")
        revived = _block(self.src, "REVIVED_AQL")
        self.assertIn("last_sync", revived)
        self.assertRegex(self.src, r"NOT ancient",
                         "the report does not surface the protected rows to the operator")

    def test_writes_stay_behind_an_explicit_confirmation(self):
        """--age-out alone must remain a dry run."""
        self.assertIn('CONFIRMED = "--yes" in sys.argv', self.src)
        self.assertIn("if not CONFIRMED:", self.src)
        self.assertIn("DRY RUN", self.src)

    def test_ageing_out_never_closes_or_deletes(self):
        """'closed' asserts the gap was fixed, which nobody verified. 'stale' asserts
        only that nobody has looked, which the timestamps prove. And nothing is removed."""
        age_out = _block(self.src, "AGE_OUT_AQL")
        self.assertIn('status: "stale"', age_out)
        self.assertNotIn('status: "closed"', age_out)
        self.assertNotIn("REMOVE", age_out.upper())
        self.assertIn("prior_status", age_out, "the transition is not reversible")

    def test_a_reversal_query_exists_and_is_printed_before_writing(self):
        self.assertIn("REVERSAL", self.src)
        reversal = re.search(r'REVERSAL = \((.*?)\)\n', self.src, re.DOTALL)
        self.assertTrue(reversal, "no REVERSAL query")
        self.assertIn("prior_status", reversal.group(1))


class TestTriageRespectsTheCollectionSchema(unittest.TestCase):
    """The status triage WRITES must be a value the schema this repo DEFINES allows.

    This is the guard for a bug that reached production use. --age-out wrote
    status "stale" while scripts/setup_schema.py declared status as the closed enum
    ["open", "closed", null] at validation level "moderate". ArangoDB rejected the
    whole batch:

        [HTTP 400][ERR 1620] drift_alerts: needs project_id + req_id;
        classification MISSING|PARTIAL; status open|closed

    Nothing was written -- the database refused it atomically, which is the system
    working. But the two files sit in the same repository, and nothing compared them,
    so a value the project itself forbids got as far as a live write. The enum has
    since been widened to include "stale"; this test is what keeps the two in step if
    either side changes again.
    """

    def setUp(self):
        self.src = _read()
        schema_path = os.path.join(REPO, "scripts", "setup_schema.py")
        with open(schema_path, encoding="utf-8") as fh:
            self.schema_src = fh.read()

    def _declared_status_enum(self):
        """The status enum from the drift_alerts block of setup_schema.py."""
        # Anchor on "rule", because setup_schema.py has TWO "drift_alerts" keys -- one
        # in INDEXES and one in SCHEMAS -- and the looser pattern matched the index
        # block, found no status enum, and failed. (Caught by the guard-the-guard test
        # below, which is the only reason this was not a silently-passing check.)
        block = re.search(r'"drift_alerts":\s*\{\s*"rule".*?"level"',
                          self.schema_src, re.DOTALL)
        self.assertTrue(block, "drift_alerts schema block not found — guard is inert")
        m = re.search(r'"status":\s*\{"enum":\s*\[(.*?)\]\}', block.group(0))
        self.assertTrue(m, "status enum not found in the drift_alerts schema")
        return {v.strip().strip('"') for v in m.group(1).split(",") if v.strip() != "None"}

    def test_the_guard_can_read_both_sides(self):
        """Guard the guard: either regex failing silently would pass everything."""
        enum = self._declared_status_enum()
        self.assertIn("open", enum)
        self.assertIn("closed", enum)
        self.assertTrue(_block(self.src, "AGE_OUT_AQL"))

    def test_the_age_out_status_is_permitted_by_the_schema(self):
        written = re.findall(r'status:\s*"([a-z]+)"', _block(self.src, "AGE_OUT_AQL"))
        self.assertTrue(written, "AGE_OUT_AQL sets no literal status")
        enum = self._declared_status_enum()
        for value in written:
            self.assertIn(
                value, enum,
                f"triage writes status {value!r}, which scripts/setup_schema.py does "
                f"not allow (enum: {sorted(enum)}). ArangoDB will reject the batch.")

    def test_the_validator_message_lists_every_allowed_status(self):
        """The message is what an operator sees when a write is refused. If it does not
        name the value that was rejected, the error sends them hunting in the wrong
        place -- which is exactly what happened here."""
        enum = self._declared_status_enum()
        msg = re.search(r'"message":\s*"drift_alerts:(.*?)",\n', self.schema_src,
                        re.DOTALL)
        self.assertTrue(msg, "drift_alerts validator message not found")
        for value in enum:
            self.assertIn(value, msg.group(1),
                          f"validator message does not mention status {value!r}")


if __name__ == "__main__":
    unittest.main()
