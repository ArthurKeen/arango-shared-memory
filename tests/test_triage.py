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
        """The undo -- now a single query, since a sweep writes alerts and nothing
        else -- printed before anything is written."""
        self.assertIn("REVERSAL_ALERTS", self.src, "no REVERSAL_ALERTS query")
        alerts = re.search(r"REVERSAL_ALERTS = \((.*?)\)\n", self.src, re.DOTALL)
        self.assertTrue(alerts, "REVERSAL_ALERTS not found")
        self.assertIn("prior_status", alerts.group(1))
        # Printed BEFORE the age-out executes, not after.
        printed = self.src.index("REVERSAL (copy before continuing)")
        written = self.src.index("db.aql.execute(AGE_OUT_AQL")
        self.assertLess(printed, written,
                        "the reversal is printed after the write it reverses")


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


    def test_every_status_the_theme_colours_is_allowed_by_the_schema(self):
        """The enum and the visualizer's status rules must agree.

        The previous round widened the enum for "stale" and left out "undocumented" --
        a value that is live in the collection AND that scripts/install_visualizer.py
        ships a colour rule for, in the same commit. The guard added then only compared
        AGE_OUT_AQL against the enum, so it had nothing to say about a value triage
        never writes.

        install_visualizer.py's rules are the closest in-repo census of the status
        values this project believes exist, which makes them the right thing to check
        against without needing a database in CI. Validation level "moderate" lets an
        already-invalid row be updated, which is why the omission broke nothing
        visible; a fresh write of that value would be refused.
        """
        vis = os.path.join(REPO, "scripts", "install_visualizer.py")
        with open(vis, encoding="utf-8") as fh:
            vis_src = fh.read()
        # Bounded by the NEXT collection key, not by the first "],\n": the theme block
        # has an earlier list (hoverInfoAttributes) that the looser pattern stopped at,
        # yielding an empty rule set. Caught by the length assertion below -- without
        # it this check would have passed vacuously, which is the failure mode it
        # exists to prevent.
        block = re.search(r'"drift_alerts":\s*\{.*?"prd_patches":', vis_src, re.DOTALL)
        self.assertTrue(block, "drift_alerts theme block not found — guard is inert")
        themed = set(re.findall(r'_rule\("status",\s*"string",\s*"=",\s*"([a-z]+)"',
                                block.group(0)))
        self.assertGreaterEqual(len(themed), 3,
                                f"theme-rule scan found only {themed} — regex is broken")
        enum = self._declared_status_enum()
        missing = sorted(themed - enum)
        self.assertEqual(
            missing, [],
            f"install_visualizer.py colours status {missing}, which "
            f"scripts/setup_schema.py does not allow (enum: {sorted(enum)}). Writing "
            "one of those values would be refused with ERR 1620.")


class TestTriageKeepsDenormalizedCountsHonest(unittest.TestCase):
    """Ageing an alert out must also fix project_registry.open_gaps.

    open_gaps is denormalized and only /prd-sync rewrites it. The first --age-out run
    swept 53 alerts and left all 8 affected projects advertising their pre-sweep count
    against 0 actual open rows (dnb-er 14, jlr-contextual-memory-fabric 10,
    network-asset-management-demo 10, domyn-gdelt 9, fraud-intelligence 6, ...).

    Permanently wrong, too: those projects were chosen precisely BECAUSE they do not
    run /prd-sync, so the one thing that would correct the counter is the one thing
    that will not happen. It is read by verify.py and by the visualizer's
    "open_gaps >= 1" orange rule.
    """

    def setUp(self):
        self.src = _read()

    def test_a_resync_query_exists_and_is_run_after_the_sweep(self):
        self.assertIn("RESYNC_GAPS_AQL", self.src,
                      "nothing repairs project_registry.open_gaps after a sweep")
        resync = _block(self.src, "RESYNC_GAPS_AQL")
        self.assertIn("project_registry", resync)
        self.assertIn("open_gaps", resync)
        # It must run on the write path, not merely be defined.
        self.assertRegex(self.src, r"db\.aql\.execute\(\s*RESYNC_GAPS_AQL",
                         "RESYNC_GAPS_AQL is defined but never executed")

    def test_the_counter_is_recomputed_not_decremented(self):
        """Subtracting the sweep size drifts further on a partial or repeated run;
        recomputing from the alerts converges."""
        resync = _block(self.src, "RESYNC_GAPS_AQL")
        self.assertRegex(resync, r'a\.status == "open"',
                         "open_gaps is not recomputed from the live open alerts")
        self.assertIn("open_gaps: live", resync)
        self.assertNotRegex(resync, r"open_gaps\s*-\s*",
                            "decrementing drifts; recompute instead")

    def test_project_ids_are_never_parsed_out_of_a_key(self):
        """A _key is a sanitised project_id + req_id and a req_id can contain the
        separator (live example: "arango-ontoextract_-6.5 VCR playback"), so splitting
        the key to recover the project is a guess.

        The previous version of this test was INERT: its assertIn was satisfied by
        unrelated pre-existing queries and its assertNotRegex was pinned to a literal
        that no longer existed, so it could not have failed on a regression. Pinned
        now to the absence of key-splitting anywhere in the file.
        """
        for pattern in (r'\.split\("_"', r"\.split\('_'", r"rsplit\(\s*[\"']_"):
            self.assertNotRegex(
                self.src, pattern,
                "a project id is being recovered by splitting a _key — unreliable; "
                "read project_id back from the row instead")


    def test_the_audit_line_pairs_by_project_not_by_position(self):
        """RESYNC_GAPS_AQL has no SORT while DRIFTED_GAPS_AQL does, so zipping the two
        result sets can print one project's name against another's previous count."""
        self.assertNotRegex(self.src, r"zip\(written, rows\)",
                            "resync audit output pairs two result sets positionally")
        self.assertIn("by_project = {r[\"project\"]: r[\"open_gaps\"] for r in written}",
                      self.src)

    def test_the_age_out_rechecks_status_at_update_time(self):
        """Selection and update are two round trips. A /prd-sync closing a gap in
        between would otherwise flip a CLOSED alert to stale with prior_status
        "closed" while it keeps closed_at, which phase3_lifecycle.py's TTL still
        acts on."""
        age_out = _block(self.src, "AGE_OUT_AQL")
        self.assertRegex(age_out, r'FILTER a\.status == "open"',
                         "AGE_OUT_AQL updates by _key without re-checking status — a "
                         "concurrently closed alert would be swept")


class TestTriageSweepIsRepairableAndReversible(unittest.TestCase):
    """A sweep makes TWO mutations, so both must be undoable and both repairable.

    Three defects from the second review of #8:

      - the printed REVERSAL undid only the alerts, while the docstring promised it
        reversed "the whole batch". Running it restored 53 rows to open and left 8
        projects at open_gaps 0, permanently.
      - a missed or failed resync could never be repaired. main() returned early when
        nothing was ancient, and swept rows are "stale" while ANCIENT_AQL selects only
        "open", so they could never be reselected. Not hypothetical: the 2026-10-23
        production sweep ran before the resync existed, leaving 8 registry rows wrong
        with no path back.
      - verify.py dropped zero-count statuses, so "0 open" was indistinguishable from
        the count no longer being reported.
    """

    def setUp(self):
        self.src = _read()

    def test_a_standalone_repair_path_exists(self):
        self.assertIn('REPAIR_GAPS = "--repair-gaps" in sys.argv', self.src,
                      "no way to repair a sweep whose counter resync was missed")
        self.assertIn("if REPAIR_GAPS:", self.src)

    def test_the_repair_set_is_derived_from_counter_drift(self):
        """Not from `status == "stale"`, which was circular: step 1 of the printed
        reversal is exactly what stops a row being stale, so step 2 found nothing and
        left open_gaps at 0 against restored open alerts. Drift is symmetric -- it
        catches the counter being too high after a missed sweep resync AND too low
        after a reversal -- and needs no memory of how the drift arose."""
        self.assertNotIn("STALE_PROJECTS_AQL", self.src,
                         "the repair still keys off stale status, which a reversal "
                         "clears before the repair can run")
        self.assertIn("DRIFTED_GAPS_AQL", self.src)
        drift = _block(self.src, "DRIFTED_GAPS_AQL")
        self.assertIn("project_registry", drift)
        self.assertIn("recorded != live", drift,
                      "the repair set is not based on a disagreement between the "
                      "recorded counter and the live count")

    def test_the_repair_honours_the_project_scope(self):
        """--project filtered the alerts but not the registry writes, so
        `--project X --age-out --yes` rewrote open_gaps for every drifted project --
        shared writes outside the stated scope, uncovered by the printed reversal."""
        drift = _block(self.src, "DRIFTED_GAPS_AQL")
        self.assertRegex(drift, r'@proj == "" OR r\.project_id == @proj',
                         "DRIFTED_GAPS_AQL ignores --project")
        # Pinned to drifted()'s OWN call. The previous assertion looked for
        # 'bind_vars={"proj": ONLY_PROJECT}' anywhere in the file, which the
        # pre-existing CONTRADICTED_AQL and ORPHANED_AQL calls already satisfy -- so it
        # could not have failed if drifted() stopped passing the scope. Second time
        # this file has shipped a test that could not fail; hence the mutation checks.
        idx = self.src.index("def drifted():")
        body = self.src[idx:idx + 400]
        self.assertIn("DRIFTED_GAPS_AQL", body)
        self.assertIn("ONLY_PROJECT", body,
                      "drifted() does not pass --project to its own query")
        self.assertIn("cutoff", body,
                      "drifted() does not pass the dormancy cutoff")

    def test_repair_gaps_refuses_to_mask_an_age_out(self):
        """The repair branch returns first, so the combination printed the sweep
        banner, did only the repair and exited 0 without sweeping anything."""
        idx = self.src.index("if REPAIR_GAPS:")
        branch = self.src[idx:idx + 500]
        self.assertIn("if AGE_OUT:", branch,
                      "--repair-gaps silently swallows --age-out")
        self.assertIn("two commands", branch)

    def test_repair_gaps_also_requires_explicit_confirmation(self):
        """It writes to shared data, so it gets the same gate as --age-out."""
        idx = self.src.index("if REPAIR_GAPS:")
        branch = self.src[idx:idx + 2200]
        self.assertIn("if not CONFIRMED:", branch)
        self.assertIn("DRY RUN", branch)


    def test_the_undo_does_not_resurrect_an_alert_prd_sync_closed(self):
        """The same race AGE_OUT_AQL guards, in the other direction, with data loss.

        The undo is pasted some time after the sweep -- deliberately; REVERSAL_GAPS
        uses DATE_NOW() for exactly that reason. A /prd-sync closing one of these rows
        in between would otherwise see it flipped back to "open" while it keeps
        closed_at, and the TTL index on drift_alerts.closed_at
        (scripts/phase3_lifecycle.py, 90 days) does not look at status -- so the
        resurrected alert is silently DELETED.
        """
        stmt = re.search(r"REVERSAL_ALERTS = \((.*?)\)\n", self.src, re.DOTALL)
        self.assertTrue(stmt, "REVERSAL_ALERTS not found — this guard is inert")
        self.assertRegex(
            stmt.group(1), r'a\.status == "stale"',
            "REVERSAL_ALERTS restores by aged_out_at alone, so a row closed between "
            "the sweep and the paste is reopened with closed_at intact and then "
            "deleted by the status-agnostic TTL")

    def test_a_re_sweep_clears_the_previous_reversal_marker(self):
        """Otherwise a second undo silently reverts nothing.

        Step 1 stamps reverted_at and deliberately leaves aged_out_at. A row swept,
        reverted, then swept again would still carry reverted_at, so the freshly
        printed undo -- which filters `reverted_at == null` -- matches zero rows,
        reports no error, and step 2 then writes open_gaps to agree with the un-undone
        state. A mixed batch is reverted silently and partially.
        """
        age_out = _block(self.src, "AGE_OUT_AQL")
        self.assertRegex(age_out, r"reverted_at:\s*null",
                         "AGE_OUT_AQL does not clear a previous reverted_at, so the "
                         "printed undo for the new batch would match nothing")


    def test_the_batch_key_does_not_depend_on_clock_resolution(self):
        """aged_out_at is second-granular, so it cannot identify a sweep.

        Two sweeps landing in the same second -- a shell loop over --project is the
        obvious way -- would share the key, and each run's printed undo would revert
        the other's alerts and counters while claiming it "cannot touch anything this
        run did not change".
        """
        self.assertIn("def batch_id()", self.src,
                      "no batch identity independent of the clock")
        self.assertRegex(self.src, r"secrets\.token_hex",
                         "the batch key has no random component, so two sweeps in the "
                         "same second collide")
        age_out = _block(self.src, "AGE_OUT_AQL")
        self.assertIn("aged_out_batch: @batch", age_out,
                      "the sweep does not stamp the batch id it printed")
        stmt = re.search(r"REVERSAL_ALERTS = \((.*?)\)\n", self.src,
                         re.DOTALL).group(1)
        self.assertIn("aged_out_batch", stmt,
                      "REVERSAL_ALERTS still keys on the second-granular timestamp")

    def test_a_fleetwide_repair_must_be_asked_for_explicitly(self):
        """--repair-gaps --yes with no scope rewrites open_gaps for every drifted
        dormant project, not only ones this tool swept. Their recorded value came from
        a /prd-sync audit (MISSING+PARTIAL) and this writes the live open-alert count
        -- a different definition, which nothing restores. Dormancy removes the
        oscillation, not the definitional mismatch."""
        self.assertIn('REPAIR_ALL = "--all" in sys.argv', self.src,
                      "no explicit opt-in for a fleet-wide rewrite")
        self.assertIn("if CONFIRMED and not ONLY_PROJECT and not REPAIR_ALL:", self.src,
                      "a fleet-wide --repair-gaps --yes runs without being asked for")
        idx = self.src.index("if CONFIRMED and not ONLY_PROJECT")
        self.assertIn("definitional mismatch", self.src[idx:idx + 1000],
                      "the refusal does not say WHY a fleet-wide rewrite is risky")



    def test_the_reversal_preserves_the_batch_marker(self):
        """Step 2 finds the batch by aged_out_at, so step 1 must not clear it."""
        stmt = re.search(r"REVERSAL_ALERTS = \((.*?)\)\n", self.src, re.DOTALL).group(1)
        self.assertNotRegex(stmt, r"aged_out_at:\s*null",
                            "step 1 clears aged_out_at, so step 2 can never find the "
                            "projects it must repair")
        self.assertIn("reverted_at", stmt,
                      "a reversal leaves no trace that it happened")


    def test_nothing_to_age_out_still_points_at_the_repair(self):
        """Once everything old is swept, this is the only reachable branch — if it
        says nothing, a wrong counter stays wrong forever."""
        idx = self.src.index("nothing older than the threshold")
        branch = self.src[idx:idx + 700]
        # Assembled by repair_hint() now, which is what guarantees every selecting
        # flag is present (--project/--all, --stale-days, --yes).
        self.assertIn("repair_hint()", branch,
                      "the nothing-to-sweep branch does not offer the repair")


class TestASweepWritesAlertsAndNothingElse(unittest.TestCase):
    """The single-mutation rule, and why it exists.

    The sweep used to also recompute project_registry.open_gaps, and that second write
    was never cleanly reversible. The counter has no per-batch identity, so each attempt
    grew another mechanism and each one had a hole:

      - recompute on undo: discarded a drift that predated the sweep entirely
      - two-statement undo keyed on aged_out_at: second-granular, so two sweeps in one
        second reverted each other
      - a batch-keyed stash: one slot per registry row, so a second sweep of the same
        project clobbered the first batch's stash; and the restore nulled the stash it
        consumed, so re-pasting the pair overwrote the value it had just restored

    Eight consecutive review rounds found a defect in that area. open_gaps is a DERIVED
    aggregate with two writers (/prd-sync's MISSING+PARTIAL audit count and this
    script's live-alert count), which is exactly what does not belong inside an undoable
    batch. So the sweep leaves it drifted on purpose and names the command that fixes
    it, and --repair-gaps -- which lists every change first and needs --all to go
    fleet-wide -- is the only path that writes it.
    """

    def setUp(self):
        self.src = _read()

    def test_the_sweep_does_not_write_project_registry(self):
        sweep = self.src[self.src.index("reason = f\"no review in"):]
        self.assertNotIn("resync_gaps(", sweep,
                         "the sweep still recomputes the counter, reintroducing a "
                         "second mutation its one-query undo cannot reverse")
        self.assertNotIn("RESYNC_GAPS_AQL", sweep)

    def test_the_counter_is_written_from_exactly_one_place(self):
        self.assertEqual(
            self.src.count("db.aql.execute(\n            RESYNC_GAPS_AQL"), 1,
            "open_gaps is written from more than one path")
        idx = self.src.index("RESYNC_GAPS_AQL, bind_vars=")
        enclosing = self.src[:idx]
        self.assertIn("def resync_gaps(label, projects):",
                      enclosing[enclosing.rindex("def "):] if "def " in enclosing
                      else "", )

    def test_no_stash_machinery_survives(self):
        """The stash was the last attempt to make the second write reversible; leaving
        any of it behind would mean the write came back."""
        for dead in ("prior_open_gaps", "REVERSAL_GAPS"):
            self.assertNotIn(dead, self.src,
                             f"{dead} survives, so the counter is being written inside "
                             "a batch again")

    def test_the_undo_is_described_as_the_whole_undo(self):
        """An operator who believes the undo is partial will go looking for a second
        step that no longer exists."""
        self.assertIn("It is the WHOLE undo", self.src)
        self.assertIn("A SWEEP WRITES ALERTS AND NOTHING ELSE", self.src)

    def test_the_sweep_reports_the_drift_it_leaves(self):
        """Leaving the counter wrong is only defensible if the run says so."""
        tail = self.src[self.src.index("aged out {len(updated)} alert(s)"):]
        self.assertIn("is now drifted for", tail,
                      "the sweep leaves open_gaps wrong without saying so")
        self.assertIn("repair_hint()", tail,
                      "the sweep does not name the command that fixes the counter")


class TestTriageDoesNotContendWithPrdSync(unittest.TestCase):
    """open_gaps has two writers with two different definitions.

    /prd-sync sets it from its audit's MISSING+PARTIAL count; the repair recomputes it
    from the live open alerts. On a project that still syncs these legitimately differ
    (project-sentinel: recorded 10, live 1, last_sync 2026-10-01), so "repairing" it
    would overwrite the audit's answer with a different question's answer and the next
    sync would put it back -- two writers oscillating, and the documented "a repeat run
    selects nothing" would be false.

    On a DORMANT project there is no contention: /prd-sync is not going to run, which
    is both why the counter went stale and why this is the only thing that can fix it.
    """

    def setUp(self):
        self.src = _read()

    def test_the_repair_is_restricted_to_dormant_projects(self):
        drift = _block(self.src, "DRIFTED_GAPS_AQL")
        self.assertIn("last_sync", drift,
                      "the repair does not check whether the project still syncs — it "
                      "would contend with /prd-sync over an active project's counter")
        self.assertRegex(drift, r"r\.project_id IN dormant",
                         "DRIFTED_GAPS_AQL does not restrict to the dormant set")

    def test_every_printed_cli_hint_carries_every_selecting_flag(self):
        """--repair-gaps re-derives its set from --project AND the --stale-days
        dormancy cutoff, so a hint that omits either points at a command that selects
        a different set than the one the operator is looking at."""
        self.assertIn("def repair_hint():", self.src,
                      "the --repair-gaps hint is assembled per call site, which is how "
                      "each one ended up dropping a different flag")
        idx = self.src.index("def repair_hint():")
        body = self.src[idx:idx + 900]
        # Pinned to the APPEND statements, not to the function's prose: an earlier
        # version asserted the bare string "--all" appeared somewhere in this window,
        # which repair_hint()'s own docstring satisfies -- so removing the flag from
        # the command left the test green. Caught by the mutation check.
        self.assertRegex(
            body, r'parts\.append\(f?"--project \{ONLY_PROJECT\}" if ONLY_PROJECT '
                  r'else "--all"\)',
            "repair_hint() does not emit --project or, unscoped, the --all that a "
            "fleet-wide --repair-gaps --yes now requires")
        self.assertRegex(body, r'parts\.append\(f"--stale-days \{STALE_DAYS\}"\)',
                         "repair_hint() omits --stale-days")
        self.assertRegex(body, r'parts\.append\("--yes"\)',
                         "repair_hint() omits --yes")
        # And nothing may hand-roll the command any more.
        self.assertEqual(
            self.src.count("triage.py --repair-gaps"), 1,
            "a --repair-gaps command is still assembled outside repair_hint()")

    def test_no_drift_is_not_reported_when_nothing_was_compared(self):
        """"The counter matches everywhere" and "the dormancy gate excluded everything
        in scope" are different answers; reporting the second as the first claims a
        consistency that was never checked."""
        self.assertIn("none compared", self.src,
                      "an empty --repair-gaps result cannot distinguish 'no drift' "
                      "from 'nothing was eligible to compare'")
        # A project with alerts but no registry row is compared by neither
        # DRIFTED_GAPS_AQL nor the active probe, so it must be named too.
        self.assertIn("but no project_registry row", self.src,
                      "a project with alerts but no registry row is never reported")
        # It must be reported WHENEVER it exists, not only when nothing was compared:
        # nesting it under the empty-result branch made it unreachable fleet-wide,
        # because that branch is never taken when any dormant project exists.
        # Anchored INSIDE the --repair-gaps branch: resync_gaps() contains its own
        # earlier "if not rows:", which a whole-file index() finds instead.
        branch = self.src[self.src.index("if REPAIR_GAPS:"):]
        self.assertLess(
            branch.index("if unregistered:"), branch.index("if not rows:"),
            "the orphaned-project note is nested inside the empty-result branch, so a "
            "fleet-wide run never reaches it")

    def test_the_empty_result_is_judged_on_what_was_compared(self):
        """Keying off what was EXCLUDED got the one case that matters backwards.

        Active and dormant partition project_registry, so a fleet-wide run almost
        always has active projects. Keying the message off `active` therefore reported
        a genuinely clean result as "none compared" -- the same false claim the block
        exists to prevent, inverted -- and made the unregistered branch unreachable
        without --project.
        """
        self.assertIn("compared = list(db.aql.execute(", self.src,
                      "the empty-result message is not based on the set that was "
                      "actually compared")
        idx = self.src.index("compared = list(db.aql.execute(")
        after = self.src[idx:idx + 1400]
        self.assertIn("if compared:", after,
                      "the clean-result branch does not key off the compared set")
        self.assertIn("matches the live count for all", after)
        # The excluded sets may only EXPLAIN an empty comparison, never decide it:
        # whatever follows "if compared:" must be the clean message, and the
        # excluded-set reporting must sit on the else side.
        clean_at = after.index("if compared:")
        else_at = after.index("else:", clean_at)
        self.assertNotIn("none compared", after[clean_at:else_at],
                         "the clean branch claims nothing was compared")
        self.assertIn("none compared", after[else_at:],
                      "the excluded-set branch does not say nothing was compared")


class TestVerifyReportsEveryAlertStatus(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "scripts", "verify.py"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_expected_statuses_are_printed_even_at_zero(self):
        self.assertIn('expected = ("open", "closed", "stale", "undocumented")', self.src,
                      "verify.py does not pin the statuses it always reports")
        self.assertIn("by_status.get(s, 0)", self.src,
                      "a zero-count status would vanish from the line")

    def test_the_expected_tuple_matches_the_schema_enum(self):
        """A third hardcoded status census, and the only one the PR left unguarded.

        setup_schema.py declares the enum, install_visualizer.py colours it, verify.py
        pins which values are always printed even at zero. The first two are
        cross-checked; without this, the next widening passes CI and silently loses the
        always-print-at-zero behaviour that this tuple is the whole point of.
        """
        m = re.search(r'expected = \((.*?)\)', self.src, re.DOTALL)
        self.assertTrue(m, "verify.py's expected-status tuple not found")
        listed = {v.strip().strip('"\'') for v in m.group(1).split(",") if v.strip()}
        with open(os.path.join(REPO, "scripts", "setup_schema.py"),
                  encoding="utf-8") as fh:
            schema_src = fh.read()
        block = re.search(r'"drift_alerts":\s*\{\s*"rule".*?"level"', schema_src,
                          re.DOTALL)
        self.assertTrue(block, "drift_alerts schema block not found — guard is inert")
        enum_m = re.search(r'"status":\s*\{"enum":\s*\[(.*?)\]\}', block.group(0))
        self.assertTrue(enum_m, "status enum not found")
        enum = {v.strip().strip('"') for v in enum_m.group(1).split(",")
                if v.strip() != "None"}
        self.assertEqual(
            listed, enum,
            f"verify.py always-prints {sorted(listed)} but the schema allows "
            f"{sorted(enum)} — a status is either never reported at zero, or reported "
            "for a value the schema forbids")

    def test_an_unexpected_or_null_status_is_still_surfaced(self):
        self.assertIn("'(no status)'", self.src,
                      "a null status would print as None")
        self.assertIn("if s not in expected", self.src,
                      "a status outside the expected set would be hidden")


if __name__ == "__main__":
    unittest.main()
