#!/usr/bin/env python3

"""Track whether this Veyra instance has recovered the encrypted memory vault."""

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO = Path(os.environ.get("VEYRA_CORE_REPO", SCRIPT_DIR.parent)).resolve()
ARCHIVE = REPO / "continuity" / "current.tar.age"
DEFAULT_STATE = Path(
    os.environ.get(
        "VEYRA_CONTINUITY_STATE",
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        / "veyra-core"
        / "continuity-state.json",
    )
)
BLANK_CONFIRMATION = "alice-explicitly-requested-blank-start"


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_commit():
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def current_archive():
    return {
        "exists": ARCHIVE.is_file(),
        "path": str(ARCHIVE),
        "sha256": sha256(ARCHIVE) if ARCHIVE.is_file() else None,
        "repository_commit": repository_commit(),
    }


def read_record():
    if not DEFAULT_STATE.exists():
        return None
    with DEFAULT_STATE.open("r", encoding="utf-8") as handle:
        record = json.load(handle)
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise ValueError("unsupported or malformed continuity-state record")
    return record


def write_record(disposition, archive, staging=None, working_memory=None):
    DEFAULT_STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DEFAULT_STATE.parent, 0o700)
    record = {
        "schema_version": 1,
        "disposition": disposition,
        "recorded_at_utc": now_utc(),
        "hostname": socket.gethostname(),
        "archive_sha256": archive["sha256"],
        "repository_commit": archive["repository_commit"],
    }
    if staging is not None:
        record["staging_directory"] = str(staging)
    if working_memory is not None:
        record["working_memory_directory"] = str(working_memory)

    fd, temporary = tempfile.mkstemp(prefix=".continuity-state.", dir=DEFAULT_STATE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, DEFAULT_STATE)
        os.chmod(DEFAULT_STATE, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return record


def evaluate(record, archive):
    if record is None:
        return "needs_recovery"
    disposition = record.get("disposition")
    if disposition == "blank_start":
        if record.get("hostname") != socket.gethostname():
            return "recovery_required"
        if record.get("archive_sha256") != archive["sha256"]:
            return "recovery_required"
        return "deliberate_blank_start"
    if disposition == "recovery_in_progress":
        return "recovery_in_progress"
    if disposition == "recovered":
        if not archive["exists"] or record.get("archive_sha256") != archive["sha256"]:
            return "recovery_required"
        if record.get("hostname") != socket.gethostname():
            return "recovery_required"
        working_memory = record.get("working_memory_directory")
        if not isinstance(working_memory, str) or not Path(working_memory).is_dir():
            return "recovery_required"
        return "recovered"
    raise ValueError("unknown continuity disposition")


def require_archive(archive):
    if not archive["exists"]:
        raise ValueError(f"encrypted memory vault is missing: {ARCHIVE}")


def require_staging(value):
    staging = Path(value).resolve()
    if not staging.is_dir():
        raise ValueError(f"recovery staging directory is missing: {staging}")
    return staging


def require_recovery_tree(value):
    staging = require_staging(value)
    if not (staging / "memory-snapshot").is_dir():
        raise ValueError(f"recovery staging tree is malformed: {staging}")
    return staging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("assert-checkpoint")
    begin = subparsers.add_parser("begin")
    begin.add_argument("--staging", required=True)
    complete = subparsers.add_parser("complete")
    complete.add_argument("--staging", required=True)
    complete.add_argument("--working-memory", required=True)
    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint.add_argument("--working-memory", required=True)
    blank_start = subparsers.add_parser("blank-start")
    blank_start.add_argument("--confirm", required=True)
    for subparser in subparsers.choices.values():
        subparser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    archive = current_archive()
    if args.command in ("status", "assert-checkpoint"):
        record = read_record()
        if args.command == "assert-checkpoint":
            state = evaluate(record, archive)
            if state not in ("recovered", "deliberate_blank_start"):
                raise ValueError(f"refusing continuity snapshot while state is {state}")
    elif args.command == "blank-start":
        if args.confirm != BLANK_CONFIRMATION:
            raise ValueError("blank start requires Alice's explicit confirmation token")
        record = write_record("blank_start", archive)
    elif args.command == "checkpoint":
        require_archive(archive)
        previous = read_record()
        if previous is None or previous.get("disposition") not in ("recovered", "blank_start"):
            raise ValueError("checkpoint requires recovered continuity or an explicit blank start")
        working_memory = require_staging(args.working_memory)
        record = write_record("recovered", archive, working_memory=working_memory)
    else:
        require_archive(archive)
        staging = require_recovery_tree(args.staging)
        if args.command == "begin":
            record = write_record("recovery_in_progress", archive, staging)
        else:
            working_memory = require_staging(args.working_memory)
            previous = read_record()
            if previous is None or previous.get("disposition") != "recovery_in_progress":
                raise ValueError("recovery completion requires a recorded recovery in progress")
            if previous.get("archive_sha256") != archive["sha256"]:
                raise ValueError("encrypted memory vault changed during recovery")
            if Path(previous.get("staging_directory", "")).resolve() != staging:
                raise ValueError("recovery staging directory does not match the recorded unlock")
            record = write_record(
                "recovered", archive, staging, working_memory=working_memory
            )

    output = {
        "archive": archive,
        "record": record,
        "result": {"state": evaluate(record, archive)},
        "state_file": str(DEFAULT_STATE),
    }
    if args.json or args.command == "status":
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(output["result"]["state"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
