"""DB-free tests for the /prd-sync evidence verifier (stdlib unittest).

Run from the repo root:  python3 -m unittest discover tests
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "templates", ".claude", "skills", "prd-sync", "check_evidence.py")


def _load():
    spec = importlib.util.spec_from_file_location("check_evidence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ce = _load()


class TestParseEvidence(unittest.TestCase):
    def test_path_line(self):
        self.assertEqual(ce.parse_evidence("src/a.ts:42"), ("src/a.ts", 42, 42))

    def test_path_range(self):
        self.assertEqual(ce.parse_evidence("src/a.ts:10-25"), ("src/a.ts", 10, 25))

    def test_bare_path(self):
        self.assertEqual(ce.parse_evidence("src/a.ts"), ("src/a.ts", None, None))

    def test_windows_style_colon_kept_in_path(self):
        # Only the trailing :NN is a line reference.
        self.assertEqual(ce.parse_evidence("a:b/c.py:7"), ("a:b/c.py", 7, 7))

    def test_non_string(self):
        self.assertIsNone(ce.parse_evidence(None))
        self.assertIsNone(ce.parse_evidence("   "))


class TestCheckEvidenceItem(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "mod.py")
        with open(self.path, "w") as fh:
            fh.write("line one with jwt token\nline two\nline three\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_verified(self):
        v, _ = ce.check_evidence_item("mod.py:2", root=self.dir.name)
        self.assertEqual(v, "verified")

    def test_missing_file(self):
        v, _ = ce.check_evidence_item("nope.py:1", root=self.dir.name)
        self.assertEqual(v, "missing_file")

    def test_line_out_of_range(self):
        v, _ = ce.check_evidence_item("mod.py:99", root=self.dir.name)
        self.assertEqual(v, "line_out_of_range")

    def test_term_found_and_not_found(self):
        v, _ = ce.check_evidence_item("mod.py:1", term="JWT", root=self.dir.name)
        self.assertEqual(v, "verified")
        v, _ = ce.check_evidence_item("mod.py:1", term="oauth", root=self.dir.name)
        self.assertEqual(v, "term_not_found")


class TestCheckClaim(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dir.name, "ok.py"), "w") as fh:
            fh.write("x = 1\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_implemented_without_evidence_fails(self):
        r = ce.check_claim({"req_id": "REQ-1", "classification": "IMPLEMENTED",
                            "evidence": []}, root=self.dir.name)
        self.assertEqual(r["verdict"], "failed")

    def test_missing_is_not_checked(self):
        r = ce.check_claim({"req_id": "REQ-2", "classification": "MISSING"},
                           root=self.dir.name)
        self.assertEqual(r["verdict"], "not_checked")

    def test_partial_without_evidence_is_legal(self):
        r = ce.check_claim({"req_id": "REQ-3", "classification": "PARTIAL"},
                           root=self.dir.name)
        self.assertEqual(r["verdict"], "not_checked")

    def test_implemented_verified(self):
        r = ce.check_claim({"req_id": "REQ-4", "classification": "IMPLEMENTED",
                            "evidence": ["ok.py:1"]}, root=self.dir.name)
        self.assertEqual(r["verdict"], "verified")


class TestMainProcess(unittest.TestCase):
    def run_main(self, payload):
        proc = subprocess.run([sys.executable, SCRIPT], input=json.dumps(payload),
                              capture_output=True, text=True)
        return proc

    def test_exit_1_on_failed_claim(self):
        proc = self.run_main({"claims": [
            {"req_id": "REQ-1", "classification": "IMPLEMENTED",
             "evidence": ["does/not/exist.ts:1"]}]})
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertEqual(out["summary"]["failed"], 1)

    def test_exit_0_on_clean_claims(self):
        proc = self.run_main({"claims": [
            {"req_id": "REQ-1", "classification": "MISSING"}]})
        self.assertEqual(proc.returncode, 0)

    def test_exit_2_on_malformed_input(self):
        proc = subprocess.run([sys.executable, SCRIPT], input="not json",
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
