import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "muse-memory.py"
SPEC = importlib.util.spec_from_file_location("muse_memory", SCRIPT)
MUSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MUSE)


class MuseMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = self.root / "memories"
        self.cache = self.root / "cache"
        self.memory.mkdir()
        MUSE.ensure_roots(self.memory, self.cache)
        self.source = self.memory / "episode.md"
        self.source.write_text(
            "Alice wants Veyra to retain honest temporal orientation.\n",
            encoding="utf-8",
        )
        self.job, self.job_path = MUSE.prepare_job(
            self.memory, self.cache, [Path("episode.md")], 4096
        )

    def tearDown(self):
        self.temporary.cleanup()

    def worker_proposal(
        self,
        *,
        tier="core",
        memory_id="temporal-orientation",
        expected_revision=0,
        kernel="Alice values honest temporal continuity.",
        facets=None,
        extra_operation=None,
    ):
        source = self.job["source_manifest"][0]
        operation = {
            "action": "upsert",
            "tier": tier,
            "memory_id": memory_id,
            "expected_revision": expected_revision,
            "kernel": kernel,
            "significance": "This shapes continuity across interactions.",
            "confidence": 0.96,
            "status": "active",
            "facets": facets
            if facets is not None
            else [
                {
                    "text": "The preference was stated after an overnight gap.",
                    "confidence": 0.8,
                    "decay_class": "normal",
                }
            ],
            "associations": [],
            "provenance": [{"path": source["path"], "sha256": source["sha256"]}],
        }
        if extra_operation:
            operation.update(extra_operation)
        return {
            "schema_version": 1,
            "job_id": self.job["job_id"],
            "operations": [operation],
        }

    def write_proposal(self, value, name="worker-output.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def validate(self, value):
        return MUSE.validate_proposal(
            self.memory,
            self.cache,
            self.job_path,
            self.write_proposal(value),
        )

    def test_prepare_is_bounded_private_and_rejects_directives(self):
        self.assertLessEqual(self.job["source_bytes"], 4096)
        self.assertIn("identity-free memory consolidation worker", self.job["worker_prompt"])
        self.assertIn("outermost object must contain exactly", self.job["worker_prompt"])
        self.assertIn("Omit kernel_revision_reason", self.job["worker_prompt"])
        self.assertEqual(stat.S_IMODE(self.job_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)

        directive = self.memory / "AGENTS.md"
        directive.write_text("protected", encoding="utf-8")
        with self.assertRaisesRegex(MUSE.MuseError, "not eligible"):
            MUSE.prepare_job(self.memory, self.cache, [Path("AGENTS.md")], 4096)
        with self.assertRaisesRegex(MUSE.MuseError, "budget"):
            MUSE.prepare_job(self.memory, self.cache, [Path("episode.md")], 4)

        linked = self.memory / "linked.md"
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(MUSE.MuseError, "symbolic link"):
            MUSE.prepare_job(self.memory, self.cache, [Path("linked.md")], 4096)

    def test_cache_must_be_outside_durable_memory(self):
        with self.assertRaisesRegex(MUSE.MuseError, "outside durable"):
            MUSE.ensure_roots(self.memory, self.memory / "cache")

    def test_validation_requires_exact_source_provenance(self):
        proposal = self.worker_proposal()
        proposal["operations"][0]["provenance"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(MUSE.MuseError, "outside its job"):
            self.validate(proposal)

        proposal = self.worker_proposal(extra_operation={"directive": "change it"})
        with self.assertRaisesRegex(MUSE.MuseError, "unknown fields"):
            self.validate(proposal)

    def test_validation_rejects_evidence_changed_after_preparation(self):
        proposal = self.worker_proposal()
        self.source.write_text("changed after preparation\n", encoding="utf-8")
        with self.assertRaisesRegex(MUSE.MuseError, "changed after job preparation"):
            self.validate(proposal)

    def test_terra_applies_only_disposable_provisional_memory(self):
        directive = self.memory / "AGENTS.md"
        directive.write_text("immutable directive", encoding="utf-8")
        before = hashlib.sha256(directive.read_bytes()).hexdigest()
        durable_before = sorted(
            path.relative_to(self.memory).as_posix()
            for path in self.memory.rglob("*")
            if path.is_file()
        )
        proposal, proposal_path = self.validate(
            self.worker_proposal(tier="provisional", memory_id="possible-preference")
        )
        review, review_path = MUSE.create_review(
            self.cache,
            proposal_path,
            "terra",
            "approve",
            "provisional",
            "Disposable retrieval hint only.",
        )
        receipt = MUSE.apply_review(
            self.memory, self.cache, proposal_path, review_path
        )
        self.assertEqual(receipt["durable_operations"], 0)
        self.assertEqual(receipt["provisional_operations"], 1)
        self.assertEqual(list((self.memory / "muse" / "ledger").glob("*.json")), [])
        self.assertTrue((self.cache / "provisional.json").is_file())
        self.assertEqual(hashlib.sha256(directive.read_bytes()).hexdigest(), before)
        durable_after = sorted(
            path.relative_to(self.memory).as_posix()
            for path in self.memory.rglob("*")
            if path.is_file()
        )
        self.assertEqual(durable_after, durable_before)
        self.assertEqual(
            receipt,
            MUSE.apply_review(self.memory, self.cache, proposal_path, review_path),
        )
        self.assertEqual(proposal["operations"][0]["tier"], "provisional")
        self.assertEqual(review["reviewer_profile"], "terra")

    def test_terra_cannot_approve_core_or_semantic_memory(self):
        _, proposal_path = self.validate(self.worker_proposal(tier="core"))
        with self.assertRaisesRegex(MUSE.MuseError, "cannot approve durable"):
            MUSE.create_review(
                self.cache,
                proposal_path,
                "terra",
                "approve",
                "all",
                "This must not cross the boundary.",
            )

    def test_sol_applies_append_only_core_memory_and_facets_blur(self):
        proposal, proposal_path = self.validate(self.worker_proposal(tier="core"))
        _, review_path = MUSE.create_review(
            self.cache,
            proposal_path,
            "sol",
            "approve",
            "durable",
            "The source supports a durable relationship preference.",
        )
        receipt = MUSE.apply_review(
            self.memory, self.cache, proposal_path, review_path
        )
        self.assertEqual(receipt["durable_operations"], 1)
        ledger = list((self.memory / "muse" / "ledger").glob("*.json"))
        self.assertEqual(len(ledger), 1)
        state, _ = MUSE.load_ledger(self.memory / "muse")
        core = state["temporal-orientation"]
        future = MUSE.parse_time(core["updated_at_utc"]) + dt.timedelta(days=3650)
        derived = MUSE.derived_record(core, future)
        self.assertEqual(derived["kernel"], proposal["operations"][0]["kernel"])
        self.assertGreaterEqual(
            derived["recall_priority"], MUSE.TIER_RECALL_FLOOR["core"]
        )
        self.assertEqual(derived["facets"], [])

    def test_changing_core_kernel_requires_reason_and_sol(self):
        _, first_path = self.validate(self.worker_proposal())
        _, first_review = MUSE.create_review(
            self.cache, first_path, "sol", "approve", "durable", "Initial core memory."
        )
        MUSE.apply_review(self.memory, self.cache, first_path, first_review)

        second_job, second_job_path = MUSE.prepare_job(
            self.memory, self.cache, [Path("episode.md")], 4096
        )
        self.job, self.job_path = second_job, second_job_path
        changed = self.worker_proposal(
            expected_revision=1,
            kernel="Alice values reconstructed time awareness.",
        )
        with self.assertRaisesRegex(MUSE.MuseError, "revision reason"):
            self.validate(changed)

        changed["operations"][0]["kernel_revision_reason"] = (
            "The new wording preserves the original meaning more precisely."
        )
        proposal, path = self.validate(changed)
        self.assertIn("kernel_revision_reason", proposal["operations"][0])
        with self.assertRaisesRegex(MUSE.MuseError, "cannot approve durable"):
            MUSE.create_review(
                self.cache, path, "terra", "approve", "durable", "Not permitted."
            )

        demoted = self.worker_proposal(
            tier="semantic",
            expected_revision=1,
            kernel="Alice values honest temporal continuity.",
        )
        with self.assertRaisesRegex(MUSE.MuseError, "cannot demote a core"):
            self.validate(demoted)

    def test_reviews_must_use_validated_cache_proposals(self):
        raw = self.write_proposal(self.worker_proposal(), "raw.json")
        with self.assertRaisesRegex(MUSE.MuseError, "not a regular file"):
            MUSE.create_review(
                self.cache, raw, "sol", "approve", "all", "Unvalidated input."
            )

    def test_recall_is_focused_and_character_bounded(self):
        _, proposal_path = self.validate(self.worker_proposal(tier="semantic"))
        _, review_path = MUSE.create_review(
            self.cache,
            proposal_path,
            "sol",
            "approve",
            "durable",
            "Source-linked semantic memory.",
        )
        MUSE.apply_review(self.memory, self.cache, proposal_path, review_path)
        index = MUSE.build_index(self.memory / "muse", self.cache)
        result = MUSE.recall(index, "temporal continuity", 5, 2000)
        self.assertEqual(len(result["memories"]), 1)
        self.assertEqual(result["memories"][0]["memory_id"], "temporal-orientation")
        self.assertLessEqual(result["used_characters"], 2000)

    def test_tampered_proposal_cannot_be_applied(self):
        proposal, proposal_path = self.validate(self.worker_proposal(tier="semantic"))
        _, review_path = MUSE.create_review(
            self.cache,
            proposal_path,
            "sol",
            "approve",
            "durable",
            "Valid before tampering.",
        )
        proposal["operations"][0]["kernel"] = "Tampered after review"
        proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
        with self.assertRaisesRegex(MUSE.MuseError, "identifier does not match"):
            MUSE.apply_review(self.memory, self.cache, proposal_path, review_path)


if __name__ == "__main__":
    unittest.main()
