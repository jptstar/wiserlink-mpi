"""Regression tests for WiserLink gas reading drift helpers."""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "wiserlink_mpi"
    / "gas_monitor.py"
)
SPEC = importlib.util.spec_from_file_location("wiserlink_gas_monitor", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
gas_monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gas_monitor)


class GasMonitorTests(unittest.TestCase):
    def test_drift_at_target_is_zero(self) -> None:
        observed = datetime(2026, 8, 25, 23, 45)
        self.assertEqual(gas_monitor.circular_drift_minutes(observed, "23:45:00"), 0)

    def test_drift_before_target_is_negative(self) -> None:
        observed = datetime(2026, 8, 25, 23, 30)
        self.assertEqual(gas_monitor.circular_drift_minutes(observed, "23:45:00"), -15)

    def test_drift_wraps_around_midnight(self) -> None:
        observed = datetime(2026, 8, 26, 0, 5)
        self.assertEqual(gas_monitor.circular_drift_minutes(observed, "23:55:00"), 10)

    def test_no_reboot_before_control_time(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 40),
            last_detected=datetime(2026, 8, 25, 16, 0),
            target_time="23:45:00",
            tolerance_minutes=15,
            control_time="23:55:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)

    def test_reboot_after_control_time_when_reading_drifted(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 55),
            last_detected=datetime(2026, 8, 25, 16, 0),
            target_time="23:45:00",
            tolerance_minutes=15,
            control_time="23:55:00",
        )
        self.assertTrue(should_reboot)
        self.assertIn("dérive", reason)

    def test_no_reboot_when_reading_is_inside_window(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 55),
            last_detected=datetime(2026, 8, 25, 23, 40),
            target_time="23:45:00",
            tolerance_minutes=15,
            control_time="23:55:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)

    def test_old_reading_can_request_correction(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 55),
            last_detected=datetime(2026, 8, 24, 23, 20),
            target_time="23:45:00",
            tolerance_minutes=15,
            control_time="23:55:00",
        )
        self.assertTrue(should_reboot)
        self.assertIn("aucune relève", reason)


if __name__ == "__main__":
    unittest.main()
