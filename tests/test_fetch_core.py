import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch-core.sh"


class FetchCoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)

    def tearDown(self):
        self.temporary.cleanup()

    def check_remote(self, remote):
        subprocess.run(
            ["git", "-C", str(self.repo), "remote", "add", "origin", remote],
            check=True,
        )
        environment = {**os.environ, "VEYRA_CORE_REPO": str(self.repo)}
        return subprocess.run(
            [str(SCRIPT), "--check-only"],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_accepts_canonical_https_remote(self):
        self.assertEqual(
            self.check_remote("https://github.com/veyra-core/veyra-core.git").returncode,
            0,
        )

    def test_accepts_canonical_ssh_remote(self):
        self.assertEqual(
            self.check_remote("git@github.com:veyra-core/veyra-core.git").returncode,
            0,
        )

    def test_rejects_different_remote(self):
        self.assertNotEqual(
            self.check_remote("https://github.com/example/veyra-core.git").returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
