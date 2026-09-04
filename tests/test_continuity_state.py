import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "continuity-state.py"


class ContinuityStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        (self.repo / "continuity").mkdir(parents=True)
        self.archive = self.repo / "continuity" / "current.tar.age"
        self.archive.write_bytes(b"first encrypted archive")
        self.state = self.root / "state" / "continuity-state.json"
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def run_state(self, *arguments):
        environment = os.environ.copy()
        environment["VEYRA_CORE_REPO"] = str(self.repo)
        environment["VEYRA_CONTINUITY_STATE"] = str(self.state)
        result = subprocess.run(
            [str(SCRIPT), *arguments, "--json"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return json.loads(result.stdout)

    def run_state_failure(self, *arguments):
        environment = os.environ.copy()
        environment["VEYRA_CORE_REPO"] = str(self.repo)
        environment["VEYRA_CONTINUITY_STATE"] = str(self.state)
        return subprocess.run(
            [str(SCRIPT), *arguments, "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_recovery_lifecycle_and_changed_archive(self):
        self.assertEqual(self.run_state("status")["result"]["state"], "needs_recovery")
        self.assertEqual(
            self.run_state("begin", "--staging", str(self.staging))["result"]["state"],
            "recovery_in_progress",
        )
        self.assertEqual(
            self.run_state("complete", "--staging", str(self.staging))["result"]["state"],
            "recovered",
        )
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            self.run_state("assert-checkpoint")["result"]["state"], "recovered"
        )
        self.archive.write_bytes(b"new encrypted archive")
        self.assertEqual(
            self.run_state("status")["result"]["state"], "recovery_required"
        )
        self.assertNotEqual(self.run_state_failure("assert-checkpoint").returncode, 0)
        self.assertEqual(
            self.run_state("checkpoint", "--working-memory", str(self.staging))["result"][
                "state"
            ],
            "recovered",
        )

    def test_explicit_blank_start(self):
        self.assertEqual(
            self.run_state(
                "blank-start", "--confirm", "alice-explicitly-requested-blank-start"
            )["result"]["state"],
            "deliberate_blank_start",
        )

    def test_blank_start_rejects_wrong_confirmation(self):
        self.assertNotEqual(
            self.run_state_failure("blank-start", "--confirm", "not-explicit").returncode,
            0,
        )

    def test_complete_requires_recorded_begin(self):
        self.assertNotEqual(
            self.run_state_failure("complete", "--staging", str(self.staging)).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
