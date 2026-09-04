import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "veyra-vault.py"


class VaultIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.config = self.root / "config"
        self.vault = self.repo / "vault"
        (self.repo / "crypto").mkdir(parents=True)
        (self.vault / "entries").mkdir(parents=True)
        (self.vault / "retired").mkdir()
        (self.vault / "recovery").mkdir()
        self.config.mkdir()

        self.alice_identity = self.root / "alice-identity.txt"
        self.run_external("age-keygen", "-o", str(self.alice_identity))
        alice_recipient = self.recipient(self.alice_identity)
        (self.repo / "crypto" / "alice-continuity.recipient").write_text(
            f"{alice_recipient}\n", encoding="ascii"
        )

        self.veyra_identity = self.config / "vault-identity.txt"
        self.run_external("age-keygen", "-o", str(self.veyra_identity))
        os.chmod(self.veyra_identity, 0o600)
        veyra_recipient = self.recipient(self.veyra_identity)
        (self.vault / "veyra-vault.recipient").write_text(
            f"{veyra_recipient}\n", encoding="ascii"
        )
        (self.vault / "GENERATION").write_text("1\n", encoding="ascii")
        self.run_external(
            "age",
            "-r",
            alice_recipient,
            "-o",
            str(self.vault / "recovery" / "current-identity.age"),
            str(self.veyra_identity),
        )
        self.environment = {
            **os.environ,
            "VEYRA_CORE_REPO": str(self.repo),
            "VEYRA_CORE_CONFIG_DIR": str(self.config),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def run_external(self, *command, check=True):
        return subprocess.run(command, check=check, capture_output=True, text=True)

    def recipient(self, identity):
        return self.run_external("age-keygen", "-y", str(identity)).stdout.strip()

    def vault_command(self, *arguments, check=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            env=self.environment,
            check=check,
            capture_output=True,
            text=True,
        )

    def test_store_materialise_inventory_forget_and_rotate(self):
        secret_value = b"synthetic-private-key-material\n"
        source = self.root / "source.key"
        source.write_bytes(secret_value)
        os.chmod(source, 0o600)

        stored = self.vault_command(
            "put-file",
            str(source),
            "--name",
            "Test identity",
            "--kind",
            "test-key",
            "--purpose",
            "Integration testing",
            "--scope",
            "Temporary test repository",
            "--fingerprint",
            "SHA256:test",
            "--authorisation",
            "Created by the integration test",
            "--track-source",
        )
        identifier = stored.stdout.split()[2].rstrip(":")

        listed = self.vault_command("list", "--json")
        records = json.loads(listed.stdout)
        self.assertEqual(records[0]["id"], identifier)
        self.assertNotIn(secret_value.decode().strip(), listed.stdout)

        output = self.root / "restored.key"
        self.vault_command("get-file", identifier, str(output))
        self.assertEqual(output.read_bytes(), secret_value)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.vault_command("audit")

        old_ciphertext = self.root / "old-secret.age"
        old_ciphertext.write_bytes(
            (self.vault / "entries" / identifier / "secret.age").read_bytes()
        )

        self.vault_command("forget", identifier, "--confirm", identifier)
        self.assertFalse(source.exists())
        self.assertFalse(output.exists())
        self.assertEqual((self.vault / "GENERATION").read_text().strip(), "2")
        self.vault_command("audit")
        cannot_recover = self.run_external(
            "age",
            "-d",
            "-i",
            str(self.veyra_identity),
            str(old_ciphertext),
            check=False,
        )
        self.assertNotEqual(cannot_recover.returncode, 0)

        recovered_identity = self.root / "recovered-veyra-identity.txt"
        self.run_external(
            "age",
            "-d",
            "-i",
            str(self.alice_identity),
            "-o",
            str(recovered_identity),
            str(self.vault / "recovery" / "current-identity.age"),
        )
        current_recipient = (
            self.vault / "veyra-vault.recipient"
        ).read_text().strip()
        self.assertEqual(self.recipient(recovered_identity), current_recipient)

        active = json.loads(self.vault_command("list", "--json").stdout)
        historical = json.loads(
            self.vault_command("list", "--all", "--json").stdout
        )
        self.assertEqual(active, [])
        self.assertEqual(historical[0]["status"], "forgotten")
        self.assertNotIn("synthetic-private-key-material", json.dumps(historical))

    def test_failed_rotation_preserves_tracked_plaintext_and_old_vault(self):
        first_source = self.root / "first.key"
        first_source.write_bytes(b"first secret\n")
        first = self.vault_command(
            "put-file",
            str(first_source),
            "--name",
            "First",
            "--kind",
            "test-key",
            "--purpose",
            "Failure testing",
            "--scope",
            "Temporary test repository",
            "--authorisation",
            "Created by the integration test",
            "--track-source",
        )
        first_id = first.stdout.split()[2].rstrip(":")
        first_output = self.root / "first-output.key"
        self.vault_command("get-file", first_id, str(first_output))

        second_source = self.root / "second.key"
        second_source.write_bytes(b"second secret\n")
        second = self.vault_command(
            "put-file",
            str(second_source),
            "--name",
            "Second",
            "--kind",
            "test-key",
            "--purpose",
            "Failure testing",
            "--scope",
            "Temporary test repository",
            "--authorisation",
            "Created by the integration test",
            "--track-source",
        )
        second_id = second.stdout.split()[2].rstrip(":")
        (self.vault / "entries" / second_id / "secret.age").write_bytes(b"corrupt")

        failed = self.vault_command(
            "forget", first_id, "--confirm", first_id, check=False
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(first_source.exists())
        self.assertTrue(first_output.exists())
        self.assertEqual((self.vault / "GENERATION").read_text().strip(), "1")
        active_ids = {
            record["id"]
            for record in json.loads(self.vault_command("list", "--json").stdout)
        }
        self.assertIn(first_id, active_ids)

    def test_plaintext_is_refused_inside_repository(self):
        source = self.repo / "plaintext.key"
        source.write_bytes(b"do not commit this\n")
        failed_put = self.vault_command(
            "put-file",
            str(source),
            "--name",
            "Unsafe",
            "--kind",
            "test-key",
            "--purpose",
            "Path testing",
            "--scope",
            "Temporary test repository",
            "--authorisation",
            "Created by the integration test",
            "--track-source",
            check=False,
        )
        self.assertNotEqual(failed_put.returncode, 0)

        safe_source = self.root / "safe-source.key"
        safe_source.write_bytes(b"safe source location\n")
        stored = self.vault_command(
            "put-file",
            str(safe_source),
            "--name",
            "Safe source",
            "--kind",
            "test-key",
            "--purpose",
            "Path testing",
            "--scope",
            "Temporary test repository",
            "--authorisation",
            "Created by the integration test",
            "--track-source",
        )
        identifier = stored.stdout.split()[2].rstrip(":")
        failed_get = self.vault_command(
            "get-file", identifier, str(self.repo / "plaintext-output.key"), check=False
        )
        self.assertNotEqual(failed_get.returncode, 0)
        self.assertFalse((self.repo / "plaintext-output.key").exists())

    def test_secret_source_disposition_is_required(self):
        source = self.root / "source.key"
        source.write_bytes(b"classified secret\n")
        result = self.vault_command(
            "put-file",
            str(source),
            "--name",
            "Unclassified source",
            "--kind",
            "test-key",
            "--purpose",
            "Argument testing",
            "--scope",
            "Temporary test repository",
            "--authorisation",
            "Created by the integration test",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
