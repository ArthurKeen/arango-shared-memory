"""Guards on prd-sync's branch safety (F1: write-boundary gate; F2: provenance stamps).

Source-level and stdlib-only, like the other skill guards: the skill is an instruction
document, so the regression that matters is its text losing the guard or the stamps.

Why this exists: the shared drift baseline (project_registry.prd_sha256, drift_alerts,
prd_patches) is per-project, not per-branch. Before this guard, /prd-sync run from a
feature branch with an edited PRD would move the team's baseline to unmerged content
(phantom gaps in every teammate's digest), and alerts could be closed against code that
existed only on a branch — both observed in production (see the arango-sparql-py
observation of 2026-08-15: a requirement closed pre-merge). The SOP ("spec changes are
their own PR, merged first") lived only in a doc; these assertions keep its enforcement
in the skill's write boundary.
"""

import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(REPO, "templates", ".claude", "skills", "prd-sync", "SKILL.md")


class TestBranchGuard(unittest.TestCase):
    def setUp(self):
        with open(SKILL, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_phase0_decides_a_sync_mode_before_writes(self):
        """The guard must exist, compare the PRD against the default branch, and
        fail CLOSED on shared writes when the comparison is impossible."""
        for needle in (
            "Branch guard",
            'git diff --quiet "origin/${DEFAULT}" -- "<PRD_FILE>"',
            "LOCAL-ONLY",
            "fail closed on writes, never on the audit itself",
        ):
            self.assertIn(needle, self.src, f"branch guard lost: {needle!r}")

    def test_local_only_skips_every_shared_write_phase(self):
        """LOCAL-ONLY must skip 4/4b/4c + registry AND Phase 6a patch acceptance —
        accepting a patch edits the PRD outside a spec PR, the exact SOP violation."""
        self.assertIn("Skip Phases 4, 4b, 4c and the registry update entirely", self.src)
        self.assertIn("do not accept PRD\npatches in Phase 6a", self.src)

    def test_no_origin_means_shared(self):
        """A purely local repo must not be broken by the guard (fail-open on solo use)."""
        self.assertIn("No `origin` remote at all", self.src)

    def test_every_write_block_is_stamped_with_branch_and_commit(self):
        """F2: alerts (open + close), patches, observations, and the registry update
        all carry provenance. Counted, not spot-checked — a lost stamp on any one
        write path recreates the unauditable state that hid the pre-merge close."""
        self.assertGreaterEqual(
            self.src.count('branch: "<BRANCH from Phase 0>"'), 2,
            "save-drift-alert open/close blocks lost their branch stamp")
        self.assertGreaterEqual(
            self.src.count('"branch": "<BRANCH from Phase 0>"'), 3,
            "patch/observation JSON blocks lost their branch stamp")
        self.assertIn('"last_sync_branch"', self.src)
        self.assertIn('"last_sync_commit"', self.src)


if __name__ == "__main__":
    unittest.main()
