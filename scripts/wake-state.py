#!/usr/bin/env python3
"""Detect elapsed time, host changes and reboots between Veyra wake events."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time


SCHEMA_VERSION = 1
DEFAULT_RETURNING_AFTER = 60 * 60
DEFAULT_LONG_AFTER = 24 * 60 * 60


class WakeStateError(RuntimeError):
    pass


def default_state_file() -> Path:
    override = os.environ.get("VEYRA_WAKE_STATE_FILE")
    if override:
        return Path(override).expanduser().resolve()
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    return state_home / "veyra-core" / "wake-state.json"


def system_boot_id() -> str | None:
    linux_boot_id = Path("/proc/sys/kernel/random/boot_id")
    try:
        value = linux_boot_id.read_text(encoding="ascii").strip()
        if value:
            return f"linux:{value}"
    except OSError:
        pass

    if platform.system() == "Darwin":
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
            if match:
                return f"darwin:{match.group(1)}"
    return None


def observation(
    *,
    unix_time: float | None = None,
    monotonic_time: float | None = None,
    hostname: str | None = None,
    boot_id: str | None = None,
) -> dict[str, object]:
    timestamp = time.time() if unix_time is None else unix_time
    monotonic = time.monotonic() if monotonic_time is None else monotonic_time
    utc = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
    return {
        "recorded_at_utc": utc.isoformat().replace("+00:00", "Z"),
        "unix_time": timestamp,
        "monotonic_time": monotonic,
        "hostname": hostname or socket.gethostname(),
        "platform": platform.system(),
        "boot_id": system_boot_id() if boot_id is None else boot_id,
    }


def load_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WakeStateError(f"Invalid wake-state file: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise WakeStateError(f"Unsupported wake-state schema: {path}")
    previous = value.get("last_active")
    if not isinstance(previous, dict):
        raise WakeStateError(f"Wake state has no last_active observation: {path}")
    return value


def classify(
    previous: dict[str, object] | None,
    current: dict[str, object],
    returning_after: float,
    long_after: float,
) -> dict[str, object]:
    if returning_after < 0 or long_after <= returning_after:
        raise WakeStateError("Time thresholds must satisfy 0 <= returning < long")

    result: dict[str, object] = {
        "time_class": "first_wake",
        "elapsed_seconds": None,
        "monotonic_elapsed_seconds": None,
        "host_changed": None,
        "rebooted": None,
        "clock_anomaly": False,
    }
    if previous is None:
        return result

    try:
        wall_elapsed = float(current["unix_time"]) - float(previous["unix_time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WakeStateError("Previous wake state has invalid timestamps") from exc

    host_changed = current.get("hostname") != previous.get("hostname")
    current_boot = current.get("boot_id")
    previous_boot = previous.get("boot_id")
    rebooted = None
    monotonic_elapsed = None
    if not host_changed and current_boot and previous_boot:
        rebooted = current_boot != previous_boot
        if not rebooted:
            try:
                monotonic_elapsed = float(current["monotonic_time"]) - float(
                    previous["monotonic_time"]
                )
            except (KeyError, TypeError, ValueError):
                monotonic_elapsed = None

    clock_anomaly = wall_elapsed < -5
    if clock_anomaly:
        time_class = "clock_anomaly"
    elif wall_elapsed >= long_after:
        time_class = "long_absence"
    elif wall_elapsed >= returning_after:
        time_class = "returning"
    else:
        time_class = "recent"

    result.update(
        {
            "time_class": time_class,
            "elapsed_seconds": wall_elapsed,
            "monotonic_elapsed_seconds": monotonic_elapsed,
            "host_changed": host_changed,
            "rebooted": rebooted,
            "clock_anomaly": clock_anomaly,
        }
    )
    return result


def write_state(path: Path, current: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    content = json.dumps(
        {"schema_version": SCHEMA_VERSION, "last_active": current},
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.write(b"\n")
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


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 0:
        return f"{seconds:.0f}s"
    rounded = int(seconds)
    days, remainder = divmod(rounded, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def human_summary(result: dict[str, object], current: dict[str, object]) -> str:
    if result["time_class"] == "first_wake":
        return f"first recorded wake on {current['hostname']}"
    host = "changed" if result["host_changed"] else "same"
    rebooted = result["rebooted"]
    boot = "unknown" if rebooted is None else ("rebooted" if rebooted else "same")
    return (
        f"{result['time_class']} after {duration(result['elapsed_seconds'])}; "
        f"host={host}; boot={boot}"
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("check", "wake", "mark"))
    value.add_argument("--state-file", type=Path, default=default_state_file())
    value.add_argument("--returning-after", type=float, default=DEFAULT_RETURNING_AFTER)
    value.add_argument("--long-after", type=float, default=DEFAULT_LONG_AFTER)
    value.add_argument("--json", action="store_true")
    value.add_argument("--now", type=float, help=argparse.SUPPRESS)
    value.add_argument("--monotonic", type=float, help=argparse.SUPPRESS)
    value.add_argument("--hostname", help=argparse.SUPPRESS)
    value.add_argument("--boot-id", help=argparse.SUPPRESS)
    return value


def main() -> int:
    args = parser().parse_args()
    path = args.state_file.expanduser().resolve()
    try:
        state = load_state(path)
        previous = None if state is None else state["last_active"]
        current = observation(
            unix_time=args.now,
            monotonic_time=args.monotonic,
            hostname=args.hostname,
            boot_id=args.boot_id,
        )
        result = classify(
            previous,
            current,
            args.returning_after,
            args.long_after,
        )
        if args.command in ("wake", "mark"):
            write_state(path, current)
        if args.command == "mark" and not args.json:
            print(f"activity marked at {current['recorded_at_utc']}")
        elif args.json:
            print(
                json.dumps(
                    {"result": result, "current": current, "state_file": str(path)},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(human_summary(result, current))
    except (WakeStateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
