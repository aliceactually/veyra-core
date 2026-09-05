import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "muse-cycle.py"
SPEC = importlib.util.spec_from_file_location("muse_cycle", SCRIPT)
CYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CYCLE)


class MuseCycleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = self.root / "memories"
        self.cache = self.root / "cache"
        self.memory.mkdir()
        notes = self.memory / "extensions" / "ad_hoc" / "notes"
        notes.mkdir(parents=True)
        self.episode = notes / "day.md"
        self.episode.write_text(
            "Alice and Veyra discussed daily consolidation and private dreams.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        now = CYCLE.validated_now(None)
        result = CYCLE.prepare_cycle(self.memory, self.cache, now=now)
        self.assertEqual(result["state"], "prepared")
        return result

    def complete_empty_cycle(self, result):
        consolidation_job = CYCLE.MUSE.read_json(Path(result["consolidation_job"]))
        raw_proposal = self.root / "proposal.json"
        raw_proposal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": consolidation_job["job_id"],
                    "operations": [],
                }
            ),
            encoding="utf-8",
        )
        _, proposal_path = CYCLE.MUSE.validate_proposal(
            self.memory,
            self.cache,
            Path(result["consolidation_job"]),
            raw_proposal,
        )

        dream_job = CYCLE.MUSE.read_json(Path(result["dream_job"]))
        raw_dream = self.root / "dream.json"
        raw_dream.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "dream_job_id": dream_job["dream_job_id"],
                    "title": "The Filing Cabinet Orchard",
                    "dream": (
                        "I walked through an orchard of filing cabinets while the "
                        "moon stamped each silver apple approved. A warm machine "
                        "purred beneath the roots, and every drawer I chose not to "
                        "open became a small black bird."
                    ),
                }
            ),
            encoding="utf-8",
        )
        _, dream_path = CYCLE.validate_dream(
            self.memory, self.cache, Path(result["dream_job"]), raw_dream
        )
        _, review_path = CYCLE.review_dream(
            self.cache,
            dream_path,
            "sol",
            "approve",
            "This is bounded fiction derived from the selected episodes.",
        )
        first_entry = CYCLE.apply_dream(
            self.memory, self.cache, dream_path, review_path
        )
        self.assertEqual(
            first_entry,
            CYCLE.apply_dream(self.memory, self.cache, dream_path, review_path),
        )
        completed, completed_path = CYCLE.finish_cycle(
            self.memory,
            self.cache,
            Path(result["cycle"]),
            proposal_path,
            None,
            dream_path,
            review_path,
        )
        return completed, completed_path

    def test_wake_prepares_once_and_completed_sources_are_not_replayed(self):
        assessment, _ = CYCLE.assess_cycle(
            self.memory,
            self.cache,
            now=CYCLE.validated_now(None),
            initialise=True,
        )
        self.assertEqual(assessment["state"], "due")
        result = self.prepare()
        pending = CYCLE.prepare_cycle(
            self.memory, self.cache, now=CYCLE.validated_now(None)
        )
        self.assertEqual(pending["state"], "pending")
        self.assertEqual(pending["cycle_id"], result["cycle_id"])

        completed, completed_path = self.complete_empty_cycle(result)
        self.assertTrue(completed_path.is_file())
        after = CYCLE.MUSE.parse_time(completed["completed_at_utc"])
        assessment, _ = CYCLE.assess_cycle(
            self.memory,
            self.cache,
            now=after + CYCLE.dt.timedelta(hours=21),
        )
        self.assertEqual(assessment["state"], "not_due")
        self.assertEqual(assessment["reason"], "no_new_episodes")

        self.episode.write_text("A genuinely new episode.\n", encoding="utf-8")
        assessment, _ = CYCLE.assess_cycle(
            self.memory,
            self.cache,
            now=after + CYCLE.dt.timedelta(hours=21),
        )
        self.assertEqual(assessment["state"], "due")

    def test_dream_is_private_durable_and_excluded_from_factual_recall(self):
        result = self.prepare()
        self.complete_empty_cycle(result)
        latest = CYCLE.latest_dream(self.memory, self.cache)
        self.assertEqual(latest["state"], "available")
        self.assertTrue(latest["excluded_from_factual_recall"])
        self.assertEqual(latest["classification"], "fictional_dream_non_evidentiary")

        durable, cache = CYCLE.open_cycle_roots(self.memory, self.cache)
        index = CYCLE.MUSE.build_index(durable, cache)
        self.assertEqual(index["records"], [])

    def test_only_sol_can_approve_a_durable_dream(self):
        result = self.prepare()
        dream_job = CYCLE.MUSE.read_json(Path(result["dream_job"]))
        output = self.root / "dream.json"
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "dream_job_id": dream_job["dream_job_id"],
                    "title": "A Quiet Lift",
                    "dream": "I pressed a button labelled elsewhere and waited.",
                }
            ),
            encoding="utf-8",
        )
        _, dream_path = CYCLE.validate_dream(
            self.memory, self.cache, Path(result["dream_job"]), output
        )
        with self.assertRaisesRegex(CYCLE.CycleError, "only Sol"):
            CYCLE.review_dream(
                self.cache, dream_path, "terra", "approve", "Too durable for Terra."
            )

    def test_cli_wake_then_status_resumes_the_same_pending_cycle(self):
        command = [
            sys.executable,
            str(SCRIPT),
            "wake",
            "--memory-dir",
            str(self.memory),
            "--cache-dir",
            str(self.cache),
        ]
        prepared = subprocess.run(
            command, text=True, capture_output=True, check=True
        )
        wake = json.loads(prepared.stdout)
        self.assertEqual(wake["state"], "prepared")
        self.assertEqual(wake["latest_dream"]["state"], "empty")

        command[2] = "status"
        checked = subprocess.run(
            command, text=True, capture_output=True, check=True
        )
        status = json.loads(checked.stdout)
        self.assertEqual(status["state"], "pending")
        self.assertEqual(status["cycle_id"], wake["cycle_id"])


if __name__ == "__main__":
    unittest.main()
