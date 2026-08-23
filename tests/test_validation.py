"""Regression tests for WiserLink UsageMeter validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "wiserlink_mpi"
    / "validation.py"
)
SPEC = importlib.util.spec_from_file_location("wiserlink_validation", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


def _meter(
    energy: float,
    power: float = 1000,
    *,
    meter_type: str = "Load1",
    energy_unit: str = "kWh",
    power_unit: str = "W",
) -> dict:
    return {
        "Type": meter_type,
        "Power": power,
        "PowerValidity": True,
        "Unit_Power": power_unit,
        "EnergyConsumed": energy,
        "EnergyValidity": True,
        "Unit_Energy": energy_unit,
    }


class UsageMeterValidationTests(unittest.TestCase):
    def test_normal_kwh_values_are_accepted(self) -> None:
        validation.validate_usage_meters([_meter(1842.37, 725)])

    def test_observed_32bit_kwh_value_is_rejected(self) -> None:
        for value in (2147483.75, 2147483.89, 2147484.24, 2147489.03):
            with self.subTest(value=value), self.assertRaises(
                validation.UsageMeterValidationError
            ):
                validation.validate_usage_meters(
                    [
                        {
                            "Type": "Load1",
                            "EnergyConsumed": value,
                            "Unit_Energy": "kWh",
                        }
                    ]
                )

    def test_exact_signed_32bit_boundary_is_rejected(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [
                    {
                        "Type": "Load1",
                        "EnergyConsumed": 2147483.647,
                        "Unit_Energy": "kWh",
                    }
                ]
            )

    def test_wh_32bit_value_is_rejected(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [
                    {
                        "Type": "Load1",
                        "EnergyConsumed": 2147483750,
                        "Unit_Energy": "Wh",
                    }
                ]
            )

    def test_unknown_energy_unit_is_treated_as_kwh_for_safety(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [{"Type": "Load1", "EnergyConsumed": 2147483.75}]
            )

    def test_negative_energy_is_rejected(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [
                    {
                        "Type": "Load1",
                        "EnergyConsumed": -1,
                        "Unit_Energy": "kWh",
                    }
                ]
            )

    def test_non_finite_measurement_is_rejected(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [
                    {
                        "Type": "Load1",
                        "Power": "NaN",
                        "Unit_Power": "W",
                    }
                ]
            )

    def test_negative_power_remains_supported(self) -> None:
        validation.validate_usage_meters(
            [
                {
                    "Type": "Electricity Meter",
                    "Power": -3500,
                    "Unit_Power": "W",
                    "EnergyConsumed": 1234.5,
                    "Unit_Energy": "kWh",
                }
            ]
        )

    def test_32bit_power_value_is_rejected(self) -> None:
        with self.assertRaises(validation.UsageMeterValidationError):
            validation.validate_usage_meters(
                [
                    {
                        "Type": "Load1",
                        "Power": 2147483647,
                        "Unit_Power": "W",
                    }
                ]
            )

    def test_large_but_valid_wh_counter_is_accepted(self) -> None:
        validation.validate_usage_meters(
            [
                {
                    "Type": "Load1",
                    "EnergyConsumed": 1000000000,
                    "Unit_Energy": "Wh",
                }
            ]
        )


class UsageMeterSnapshotTests(unittest.TestCase):
    def test_normal_increment_is_coherent(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.000, 1200)],
            [_meter(100.010, 1300)],
            30,
        )
        self.assertEqual(anomalies, ())

    def test_counter_decrease_requires_confirmation(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.0)],
            [_meter(0.0)],
            30,
        )
        self.assertIn("energy_decrease:1", anomalies)

    def test_transient_zero_then_real_value_is_detected_as_jump(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(0.0, 1000)],
            [_meter(1200.0, 1000)],
            1,
        )
        self.assertIn("energy_jump:1", anomalies)

    def test_large_unexplained_energy_jump_requires_confirmation(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.0, 500)],
            [_meter(120.0, 500)],
            30,
        )
        self.assertIn("energy_jump:1", anomalies)

    def test_high_power_explains_large_increment(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.0, 100000)],
            [_meter(102.0, 100000)],
            60,
        )
        self.assertEqual(anomalies, ())

    def test_confirmed_new_baseline_is_coherent_between_confirmations(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(1200.000, 1000)],
            [_meter(1200.001, 1000)],
            1,
        )
        self.assertEqual(anomalies, ())

    def test_meter_count_change_requires_confirmation(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.0)],
            [_meter(100.0), _meter(20.0, meter_type="Load2")],
            30,
        )
        self.assertEqual(anomalies, ("structure:count",))

    def test_meter_type_change_requires_confirmation(self) -> None:
        anomalies = validation.snapshot_anomalies(
            [_meter(100.0, meter_type="Load1")],
            [_meter(100.0, meter_type="Load2")],
            30,
        )
        self.assertIn("structure:1", anomalies)

    def test_volume_increment_is_not_checked_against_electrical_power(self) -> None:
        old = _meter(10.0, energy_unit="m3")
        new = _meter(20.0, energy_unit="m3")
        anomalies = validation.snapshot_anomalies([old], [new], 1)
        self.assertEqual(anomalies, ())

    def test_volume_counter_decrease_still_requires_confirmation(self) -> None:
        old = _meter(10.0, energy_unit="m3")
        new = _meter(0.0, energy_unit="m3")
        anomalies = validation.snapshot_anomalies([old], [new], 30)
        self.assertIn("energy_decrease:1", anomalies)

    def test_invalid_energy_flag_skips_continuity_check(self) -> None:
        old = _meter(100.0)
        new = _meter(0.0)
        new["EnergyValidity"] = False
        anomalies = validation.snapshot_anomalies([old], [new], 30)
        self.assertEqual(anomalies, ())


if __name__ == "__main__":
    unittest.main()
