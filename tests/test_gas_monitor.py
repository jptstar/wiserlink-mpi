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
            now=datetime(2026, 8, 25, 23, 34),
            last_detected=datetime(2026, 8, 25, 16, 0),
            target_time="23:45:00",
            tolerance_minutes=10,
            control_time="23:35:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)

    def test_reboot_at_early_control_time_when_cycle_is_drifted(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 35),
            last_detected=datetime(2026, 8, 24, 16, 0),
            target_time="23:45:00",
            tolerance_minutes=10,
            control_time="23:35:00",
        )
        self.assertTrue(should_reboot)
        self.assertIn("dérive", reason)

    def test_previous_2347_reading_does_not_reboot_at_2335(self) -> None:
        """Absence of today's still-not-due reading must not cause a reboot."""
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 35),
            last_detected=datetime(2026, 8, 24, 23, 47),
            target_time="23:45:00",
            tolerance_minutes=10,
            control_time="23:35:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)

    def test_no_late_reboot_at_or_after_target(self) -> None:
        for minute in (45, 46, 55):
            with self.subTest(minute=minute):
                should_reboot, reason = gas_monitor.should_correct_drift(
                    now=datetime(2026, 8, 25, 23, minute),
                    last_detected=datetime(2026, 8, 25, 16, 0),
                    target_time="23:45:00",
                    tolerance_minutes=10,
                    control_time="23:35:00",
                )
                self.assertFalse(should_reboot)
                self.assertIsNone(reason)

    def test_learned_2347_cycle_is_used_as_reference(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 35),
            last_detected=datetime(2026, 8, 24, 23, 56),
            target_time="23:45:00",
            reference_time="23:47:00",
            tolerance_minutes=10,
            control_time="23:35:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)

    def test_learned_cycle_reboots_only_after_true_reference_drift(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 35),
            last_detected=datetime(2026, 8, 24, 23, 58),
            target_time="23:45:00",
            reference_time="23:47:00",
            tolerance_minutes=10,
            control_time="23:35:00",
        )
        self.assertTrue(should_reboot)
        self.assertIn("référence 23:47:00", reason)

    def test_invalid_late_control_time_disables_automatic_reboot(self) -> None:
        should_reboot, reason = gas_monitor.should_correct_drift(
            now=datetime(2026, 8, 25, 23, 55),
            last_detected=datetime(2026, 8, 25, 16, 0),
            target_time="23:45:00",
            tolerance_minutes=10,
            control_time="23:55:00",
        )
        self.assertFalse(should_reboot)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
