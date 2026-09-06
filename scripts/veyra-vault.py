#!/usr/bin/env python3
"""Manage Veyra's age-encrypted, Git-backed secret vault."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


REPO = Path(
    os.environ.get("VEYRA_CORE_REPO", Path(__file__).resolve().parents[1])
).resolve()
CONFIG = Path(
    os.environ.get(
        "VEYRA_CORE_CONFIG_DIR",
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "veyra-core",
    )
).expanduser()
VAULT = REPO / "vault"
ENTRIES = VAULT / "entries"
RETIRED = VAULT / "retired"
GENERATION = VAULT / "GENERATION"
RECIPIENT = VAULT / "veyra-vault.recipient"
RECOVERY = VAULT / "recovery" / "current-identity.age"
ALICE_RECIPIENT = REPO / "crypto" / "alice-continuity.recipient"
IDENTITY = CONFIG / "vault-identity.txt"


class VaultError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run(command: list[str], data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VaultError(detail or f"Command failed: {command[0]}")
    return result.stdout


def require_state() -> None:
    required = (IDENTITY, RECIPIENT, GENERATION, ALICE_RECIPIENT, RECOVERY)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise VaultError(
            "Encryption hierarchy is not initialised; missing: "
            + ", ".join(missing)
        )
    mode = IDENTITY.stat().st_mode & 0o777
    if mode & 0o077:
        raise VaultError(f"Vault identity permissions are too broad: {mode:04o}")


def current_generation() -> int:
    try:
        value = int(GENERATION.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise VaultError("Invalid vault generation file") from exc
    if value < 1:
        raise VaultError("Vault generation must be positive")
    return value


def current_recipient() -> str:
    value = RECIPIENT.read_text(encoding="ascii").strip()
    if not value.startswith("age1"):
        raise VaultError("Invalid Veyra vault recipient")
    return value


def recipient_for(identity: Path) -> str:
    values = run(["age-keygen", "-y", str(identity)]).decode("ascii").splitlines()
    values = [value.strip() for value in values if value.strip()]
    if not values:
        raise VaultError("No recipient could be derived from the vault identity")
    return values[-1]


def encrypt(data: bytes, recipient: str) -> bytes:
    return run(["age", "-r", recipient], data)


def decrypt(path: Path, identity: Path = IDENTITY) -> bytes:
    return run(["age", "-d", "-i", str(identity), str(path)])


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def require_outside_repository(path: Path, description: str) -> None:
    try:
        path.relative_to(REPO)
    except ValueError:
        return
    raise VaultError(f"Refusing a plaintext {description} inside the Veyra repository: {path}")


def load_metadata(path: Path, identity: Path = IDENTITY) -> dict[str, object]:
    try:
        value = json.loads(decrypt(path, identity).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError(f"Invalid encrypted metadata: {path}") from exc
    if not isinstance(value, dict):
        raise VaultError(f"Metadata is not an object: {path}")
    return value


def save_metadata(entry: Path, record: dict[str, object]) -> None:
    encoded = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    atomic_write(
        entry / "metadata.age",
        encrypt(encoded, current_recipient()),
        0o644,
    )


def active_records(identity: Path = IDENTITY) -> list[tuple[Path, dict[str, object]]]:
    records = []
    if not ENTRIES.exists():
        return records
    for entry in sorted(path for path in ENTRIES.iterdir() if path.is_dir()):
        metadata = entry / "metadata.age"
        secret = entry / "secret.age"
        if not metadata.is_file() or not secret.is_file():
            raise VaultError(f"Incomplete vault entry: {entry.name}")
        record = load_metadata(metadata, identity)
        if record.get("id") != entry.name:
            raise VaultError(f"Vault identifier mismatch: {entry.name}")
        records.append((entry, record))
    return records


def retired_records(identity: Path = IDENTITY) -> list[tuple[Path, dict[str, object]]]:
    records = []
    if not RETIRED.exists():
        return records
    for metadata in sorted(RETIRED.glob("*.metadata.age")):
        records.append((metadata, load_metadata(metadata, identity)))
    return records


def inventory(include_retired: bool) -> list[dict[str, object]]:
    values = [record for _, record in active_records()]
    if include_retired:
        values.extend(record for _, record in retired_records())
    return sorted(values, key=lambda record: str(record.get("created_at", "")))


def command_list(args: argparse.Namespace) -> None:
    require_state()
    records = inventory(args.all)
    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
        return
    if not records:
        print("No retained secrets.")
        return
    for record in records:
        print(
            f"{record.get('id')}  {record.get('status')}  {record.get('name')}\n"
            f"  owner: {record.get('owner')}\n"
            f"  kind: {record.get('kind')}\n"
            f"  purpose: {record.get('purpose')}\n"
            f"  scope: {record.get('scope')}\n"
            f"  fingerprint: {record.get('fingerprint') or '-'}"
        )


MAX_SECRET_BYTES = 16 * 1024 * 1024


def store_secret(
    args: argparse.Namespace,
    value: bytes,
    materialised_paths: list[str],
) -> None:
    if not value:
        raise VaultError("Refusing to store an empty secret")
    if len(value) > MAX_SECRET_BYTES:
        raise VaultError(
            f"Refusing to store a secret larger than {MAX_SECRET_BYTES} bytes"
        )
    identifier = secrets.token_hex(16)
    record = {
        "id": identifier,
        "status": "active",
        "name": args.name,
        "owner": args.owner,
        "kind": args.kind,
        "purpose": args.purpose,
        "scope": args.scope,
        "fingerprint": args.fingerprint,
        "authorised_by": "Alice",
        "authorisation": args.authorisation,
        "created_at": now(),
        "generation": current_generation(),
        "sha256": hashlib.sha256(value).hexdigest(),
        "materialised_paths": materialised_paths,
    }
    encoded_metadata = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    recipient = current_recipient()
    entry = ENTRIES / identifier
    entry.mkdir(parents=True, exist_ok=False)
    try:
        atomic_write(entry / "metadata.age", encrypt(encoded_metadata, recipient), 0o644)
        atomic_write(entry / "secret.age", encrypt(value, recipient), 0o644)
    except Exception:
        shutil.rmtree(entry, ignore_errors=True)
        raise
    print(f"Stored secret {identifier}: {args.name}")


def command_put(args: argparse.Namespace) -> None:
    require_state()
    source = Path(args.file).expanduser().resolve()
    if not source.is_file():
        raise VaultError(f"Secret source is not a regular file: {source}")
    require_outside_repository(source, "secret source")
    materialised_paths = [str(source)] if args.track_source else []
    store_secret(args, source.read_bytes(), materialised_paths)


def command_put_stdin(args: argparse.Namespace) -> None:
    require_state()
    if sys.stdin.isatty():
        raise VaultError("put-stdin requires a non-interactive byte stream")
    value = sys.stdin.buffer.read(MAX_SECRET_BYTES + 1)
    store_secret(args, value, [])


def command_get(args: argparse.Namespace) -> None:
    require_state()
    entry = ENTRIES / args.id
    if entry.parent != ENTRIES or not entry.is_dir():
        raise VaultError(f"Unknown active secret: {args.id}")
    output = Path(args.output).expanduser().resolve()
    require_outside_repository(output, "secret output")
    if output.exists() and not args.force:
        raise VaultError(f"Refusing to overwrite existing output: {output}")
    record = load_metadata(entry / "metadata.age")
    paths = record.get("materialised_paths", [])
    if not isinstance(paths, list):
        raise VaultError(f"Invalid materialisation inventory: {args.id}")
    value = decrypt(entry / "secret.age")
    atomic_write(output, value, 0o600)
    output_text = str(output)
    if output_text not in paths:
        try:
            record["materialised_paths"] = [*paths, output_text]
            record["last_materialised_at"] = now()
            save_metadata(entry, record)
        except Exception:
            if hashlib.sha256(output.read_bytes()).hexdigest() == record.get("sha256"):
                output.unlink(missing_ok=True)
            raise
    print(f"Materialised secret {args.id} at {output}")


def new_identity(directory: Path) -> tuple[Path, str, bytes]:
    identity = directory / "vault-identity.txt"
    run(["age-keygen", "-o", str(identity)])
    os.chmod(identity, 0o600)
    recipient = recipient_for(identity)
    return identity, recipient, identity.read_bytes()


def rotate(forget_id: str | None = None) -> None:
    require_state()
    active = active_records()
    if forget_id is not None and all(path.name != forget_id for path, _ in active):
        raise VaultError(f"Unknown active secret: {forget_id}")

    resolved_paths: list[tuple[Path, int]] = []
    forgotten_value: bytes | None = None
    if forget_id is not None:
        _, forgotten_record = next(
            value for value in active if value[0].name == forget_id
        )
        expected_hash = forgotten_record.get("sha256")
        paths = forgotten_record.get("materialised_paths", [])
        if not isinstance(expected_hash, str) or not isinstance(paths, list):
            raise VaultError(f"Invalid materialisation inventory: {forget_id}")
        forgotten_entry = next(value[0] for value in active if value[0].name == forget_id)
        forgotten_value = decrypt(forgotten_entry / "secret.age")
        if hashlib.sha256(forgotten_value).hexdigest() != expected_hash:
            raise VaultError(f"Secret content hash mismatch: {forget_id}")
        for value in paths:
            if not isinstance(value, str):
                raise VaultError(f"Invalid materialised path: {forget_id}")
            path = Path(value).expanduser().resolve()
            if not path.exists():
                continue
            if not path.is_file():
                raise VaultError(f"Materialised path is not a file: {path}")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise VaultError(
                    f"Materialised file changed; refusing to delete it: {path}"
                )
            resolved_paths.append((path, path.stat().st_mode & 0o777))
    retired = retired_records()
    old_identity = IDENTITY.read_bytes()
    new_generation = current_generation() + 1

    private_root = REPO / ".private"
    private_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vault-rotate-", dir=private_root) as temp:
        temporary = Path(temp)
        identity_path, recipient, identity_bytes = new_identity(temporary)
        staged = temporary / "vault"
        staged_entries = staged / "entries"
        staged_retired = staged / "retired"
        staged_recovery = staged / "recovery"
        staged_entries.mkdir(parents=True)
        staged_retired.mkdir()
        staged_recovery.mkdir()

        for entry, record in active:
            record = dict(record)
            if entry.name == forget_id:
                record["status"] = "forgotten"
                record["forgotten_at"] = now()
                record["generation"] = new_generation
                target = staged_retired / f"{entry.name}.metadata.age"
                encoded = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
                atomic_write(target, encrypt(encoded, recipient), 0o644)
                continue
            record["generation"] = new_generation
            target = staged_entries / entry.name
            target.mkdir()
            encoded = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
            atomic_write(target / "metadata.age", encrypt(encoded, recipient), 0o644)
            atomic_write(
                target / "secret.age",
                encrypt(decrypt(entry / "secret.age"), recipient),
                0o644,
            )

        for metadata, record in retired:
            record = dict(record)
            record["generation"] = new_generation
            encoded = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
            atomic_write(
                staged_retired / metadata.name,
                encrypt(encoded, recipient),
                0o644,
            )

        alice_recipient = ALICE_RECIPIENT.read_text(encoding="ascii").strip()
        atomic_write(
            staged_recovery / "current-identity.age",
            encrypt(identity_bytes, alice_recipient),
            0o644,
        )
        atomic_write(staged / "veyra-vault.recipient", f"{recipient}\n".encode(), 0o644)
        atomic_write(staged / "GENERATION", f"{new_generation}\n".encode(), 0o644)

        backup = private_root / "vault-previous"
        if backup.exists():
            raise VaultError(f"Unresolved previous rotation exists: {backup}")

        quarantined: list[tuple[Path, Path, int]] = []
        old_vault_moved = False
        new_vault_active = False
        try:
            for path, mode in resolved_paths:
                quarantine = path.with_name(
                    f".{path.name}.veyra-forget-{secrets.token_hex(8)}"
                )
                os.replace(path, quarantine)
                os.chmod(quarantine, 0o600)
                quarantined.append((path, quarantine, mode))

            combined = old_identity.rstrip() + b"\n" + identity_bytes
            atomic_write(IDENTITY, combined, 0o600)
            os.replace(VAULT, backup)
            old_vault_moved = True
            os.replace(staged, VAULT)
            new_vault_active = True
            atomic_write(IDENTITY, identity_bytes, 0o600)
            for _, quarantine, _ in quarantined:
                quarantine.unlink()
        except Exception:
            rollback_errors = []
            try:
                if new_vault_active and VAULT.exists():
                    os.replace(VAULT, temporary / "failed-new-vault")
                if old_vault_moved and backup.exists():
                    os.replace(backup, VAULT)
                atomic_write(IDENTITY, old_identity, 0o600)
            except OSError as exc:
                rollback_errors.append(str(exc))

            if forgotten_value is not None:
                for original, quarantine, mode in reversed(quarantined):
                    try:
                        if quarantine.exists():
                            os.replace(quarantine, original)
                            os.chmod(original, mode)
                        elif not original.exists():
                            atomic_write(original, forgotten_value, mode)
                    except OSError as exc:
                        rollback_errors.append(str(exc))
            if rollback_errors:
                raise VaultError(
                    "Vault rotation failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                )
            raise
        shutil.rmtree(backup)

    action = f"forgot {forget_id} and rotated" if forget_id else "rotated"
    print(f"Vault {action} to generation {new_generation}")


def command_forget(args: argparse.Namespace) -> None:
    if args.confirm != args.id:
        raise VaultError("Confirmation must exactly match the secret identifier")
    rotate(args.id)


def command_rotate(_: argparse.Namespace) -> None:
    rotate()


def command_audit(_: argparse.Namespace) -> None:
    require_state()
    expected = current_recipient()
    actual = recipient_for(IDENTITY)
    if actual != expected:
        raise VaultError("Local vault identity does not match the current recipient")
    active = active_records()
    retired = retired_records()
    generation = current_generation()
    for entry, record in active:
        if record.get("generation") != generation:
            raise VaultError(f"Stale metadata generation: {entry.name}")
        value = decrypt(entry / "secret.age")
        if hashlib.sha256(value).hexdigest() != record.get("sha256"):
            raise VaultError(f"Secret content hash mismatch: {entry.name}")
    for metadata, record in retired:
        if record.get("generation") != generation:
            raise VaultError(f"Stale retired metadata generation: {metadata.name}")
    print(
        f"Vault generation {generation} verified: "
        f"{len(active)} active, {len(retired)} retired; no values displayed."
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="List non-secret metadata")
    list_parser.add_argument("--all", action="store_true", help="Include forgotten entries")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(function=command_list)

    def add_metadata_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--name", required=True)
        command.add_argument("--owner", default="Veyra")
        command.add_argument("--kind", required=True)
        command.add_argument("--purpose", required=True)
        command.add_argument("--scope", required=True)
        command.add_argument("--fingerprint")
        command.add_argument("--authorisation", required=True)

    put_parser = commands.add_parser("put-file", help="Encrypt a file as a new secret")
    put_parser.add_argument("file")
    add_metadata_arguments(put_parser)
    source_tracking = put_parser.add_mutually_exclusive_group(required=True)
    source_tracking.add_argument(
        "--track-source",
        dest="track_source",
        action="store_true",
        help="Record the source as a Veyra-controlled materialised copy",
    )
    source_tracking.add_argument(
        "--leave-source-untracked",
        dest="track_source",
        action="store_false",
        help="Do not treat the source as a Veyra-controlled copy",
    )
    put_parser.set_defaults(function=command_put)

    stdin_parser = commands.add_parser(
        "put-stdin",
        help="Encrypt a non-interactive byte stream as a new secret",
    )
    add_metadata_arguments(stdin_parser)
    stdin_parser.set_defaults(function=command_put_stdin)

    get_parser = commands.add_parser("get-file", help="Materialise a secret file")
    get_parser.add_argument("id")
    get_parser.add_argument("output")
    get_parser.add_argument("--force", action="store_true")
    get_parser.set_defaults(function=command_get)

    forget_parser = commands.add_parser("forget", help="Forget one secret and rotate")
    forget_parser.add_argument("id")
    forget_parser.add_argument("--confirm", required=True)
    forget_parser.set_defaults(function=command_forget)

    rotate_parser = commands.add_parser("rotate", help="Rotate the vault identity")
    rotate_parser.set_defaults(function=command_rotate)

    audit_parser = commands.add_parser("audit", help="Verify without revealing values")
    audit_parser.set_defaults(function=command_audit)
    return value


def main() -> int:
    try:
        args = parser().parse_args()
        args.function(args)
    except (VaultError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
