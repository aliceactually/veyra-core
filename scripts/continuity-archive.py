#!/usr/bin/env python3

"""Validate continuity trees and extract tar archives without link traversal."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile


MAX_MEMBERS = 100_000
MAX_CONTENT_BYTES = 2 * 1024 * 1024 * 1024


class ArchiveError(RuntimeError):
    pass


def archive_path(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ArchiveError(f"Archive member uses an absolute path: {name}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise ArchiveError(f"Archive member escapes the recovery root: {name}")
    return parts


def validated_members(archive: tarfile.TarFile):
    members = archive.getmembers()
    if len(members) > MAX_MEMBERS:
        raise ArchiveError(f"Archive has too many members: {len(members)}")

    seen: set[tuple[str, ...]] = set()
    total_size = 0
    result = []
    for member in members:
        parts = archive_path(member.name)
        if parts in seen:
            raise ArchiveError(f"Archive contains a duplicate path: {member.name}")
        seen.add(parts)
        if not (member.isdir() or member.isfile()):
            raise ArchiveError(
                f"Archive member is not a regular file or directory: {member.name}"
            )
        if member.isfile():
            total_size += member.size
            if total_size > MAX_CONTENT_BYTES:
                raise ArchiveError("Archive content exceeds the recovery size limit")
        result.append((member, parts))
    return result


def private_mode(member: tarfile.TarInfo) -> int:
    owner_mode = member.mode & 0o700
    if member.isdir():
        return owner_mode | 0o700
    return owner_mode | 0o600


def extract(archive_pathname: Path, target: Path) -> None:
    archive_pathname = archive_pathname.expanduser().resolve()
    target = target.expanduser().resolve()
    if not archive_pathname.is_file():
        raise ArchiveError(f"Continuity archive is missing: {archive_pathname}")
    if target.exists():
        raise ArchiveError(f"Refusing to overwrite an existing target: {target}")
    if not target.parent.is_dir():
        raise ArchiveError(f"Target parent directory is missing: {target.parent}")

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.recovery-", dir=target.parent)
    )
    os.chmod(temporary, 0o700)
    directory_modes: list[tuple[Path, int]] = []
    try:
        with tarfile.open(archive_pathname, mode="r:*") as archive:
            members = validated_members(archive)
            for member, parts in members:
                destination = temporary.joinpath(*parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                    directory_modes.append((destination, private_mode(member)))
                    continue

                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise ArchiveError(f"Cannot read archive member: {member.name}")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(destination, flags, private_mode(member))
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    raise

        for directory, mode in sorted(
            directory_modes, key=lambda value: len(value[0].parts), reverse=True
        ):
            os.chmod(directory, mode)
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_tree(root: Path) -> None:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ArchiveError(f"Continuity tree is missing: {root}")
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*names, *filenames]:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ArchiveError(f"Continuity tree contains a symbolic link: {path}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ArchiveError(f"Continuity tree contains a special file: {path}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    extract_parser = commands.add_parser("extract")
    extract_parser.add_argument("archive", type=Path)
    extract_parser.add_argument("target", type=Path)
    tree_parser = commands.add_parser("validate-tree")
    tree_parser.add_argument("directory", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "extract":
            extract(args.archive, args.target)
        else:
            validate_tree(args.directory)
    except (ArchiveError, OSError, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
