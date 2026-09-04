import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wake-state.py"
SPEC = importlib.util.spec_from_file_location("wake_state", SCRIPT)
WAKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WAKE)


def sample(unix_time, monotonic_time, hostname="host-a", boot_id="boot-a"):
    return WAKE.observation(
        unix_time=unix_time,
        monotonic_time=monotonic_time,
        hostname=hostname,
        boot_id=boot_id,
    )


class WakeStateTest(unittest.TestCase):
    def test_first_wake(self):
        result = WAKE.classify(None, sample(100, 50), 3600, 86400)
        self.assertEqual(result["time_class"], "first_wake")
        self.assertIsNone(result["elapsed_seconds"])

    def test_elapsed_time_classes(self):
        previous = sample(100, 50)
        recent = WAKE.classify(previous, sample(200, 150), 3600, 86400)
        returning = WAKE.classify(previous, sample(7300, 7250), 3600, 86400)
        long_absence = WAKE.classify(
            previous, sample(100000, 99950), 3600, 86400
        )
        self.assertEqual(recent["time_class"], "recent")
        self.assertEqual(returning["time_class"], "returning")
        self.assertEqual(long_absence["time_class"], "long_absence")
        self.assertEqual(returning["monotonic_elapsed_seconds"], 7200)

    def test_host_change_and_reboot_are_independent(self):
        previous = sample(100, 50)
        changed = WAKE.classify(
            previous,
            sample(200, 150, hostname="host-b", boot_id="boot-b"),
            3600,
            86400,
        )
        rebooted = WAKE.classify(
            previous,
            sample(200, 25, hostname="host-a", boot_id="boot-b"),
            3600,
            86400,
        )
        self.assertTrue(changed["host_changed"])
        self.assertIsNone(changed["rebooted"])
        self.assertFalse(rebooted["host_changed"])
        self.assertTrue(rebooted["rebooted"])
        self.assertIsNone(rebooted["monotonic_elapsed_seconds"])

    def test_clock_anomaly(self):
        result = WAKE.classify(sample(100, 50), sample(50, 100), 3600, 86400)
        self.assertEqual(result["time_class"], "clock_anomaly")
        self.assertTrue(result["clock_anomaly"])

    def test_state_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "wake-state.json"
            current = sample(100, 50)
            WAKE.write_state(path, current)
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(state["last_active"]["hostname"], "host-a")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(WAKE.WakeStateError):
            WAKE.classify(None, sample(100, 50), 3600, 3600)


if __name__ == "__main__":
    unittest.main()
