import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "continuity-archive.py"


class ContinuityArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "continuity.tar"
        self.target = self.root / "restored"

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, *arguments, check=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            check=check,
            capture_output=True,
            text=True,
        )

    def write_archive(self, members):
        with tarfile.open(self.archive, "w") as archive:
            for member, content in members:
                archive.addfile(member, io.BytesIO(content) if content is not None else None)

    def test_extracts_regular_private_tree_atomically(self):
        directory = tarfile.TarInfo("memory-snapshot")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        content = b"recovered memory\n"
        file_info = tarfile.TarInfo("memory-snapshot/MEMORY.md")
        file_info.size = len(content)
        file_info.mode = 0o644
        self.write_archive([(directory, None), (file_info, content)])

        self.run_script("extract", self.archive, self.target)
        recovered = self.target / "memory-snapshot" / "MEMORY.md"
        self.assertEqual(recovered.read_bytes(), content)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(recovered.stat().st_mode), 0o600)

    def test_rejects_traversal_and_leaves_no_target(self):
        content = b"escape\n"
        member = tarfile.TarInfo("../escape.txt")
        member.size = len(content)
        self.write_archive([(member, content)])

        result = self.run_script("extract", self.archive, self.target, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_rejects_links(self):
        member = tarfile.TarInfo("memory-snapshot/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        self.write_archive([(member, None)])

        result = self.run_script("extract", self.archive, self.target, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())

    def test_rejects_absolute_duplicate_and_hardlink_members(self):
        cases = []

        absolute = tarfile.TarInfo("/tmp/veyra-absolute")
        absolute.size = 1
        cases.append([(absolute, b"x")])

        first = tarfile.TarInfo("memory-snapshot/duplicate")
        first.size = 1
        second = tarfile.TarInfo("memory-snapshot/duplicate")
        second.size = 1
        cases.append([(first, b"a"), (second, b"b")])

        hardlink = tarfile.TarInfo("memory-snapshot/hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "memory-snapshot/other"
        cases.append([(hardlink, None)])

        for index, members in enumerate(cases):
            with self.subTest(index=index):
                self.archive.unlink(missing_ok=True)
                target = self.root / f"restored-{index}"
                self.write_archive(members)
                result = self.run_script("extract", self.archive, target, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(target.exists())

    def test_validate_tree_rejects_symbolic_links(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "file").write_text("content", encoding="utf-8")
        os.symlink(tree / "file", tree / "link")
        result = self.run_script("validate-tree", tree, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
