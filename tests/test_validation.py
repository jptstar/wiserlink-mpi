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


class UsageMeterValidationTests(unittest.TestCase):
    def test_normal_kwh_values_are_accepted(self) -> None:
        validation.validate_usage_meters(
            [
                {
                    "Type": "Load1",
                    "Power": 725,
                    "Unit_Power": "W",
                    "EnergyConsumed": 1842.37,
                    "Unit_Energy": "kWh",
                }
            ]
        )

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


if __name__ == "__main__":
    unittest.main()
