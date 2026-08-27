"""Regression tests for stable WiserLink UsageMeter identities."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

COMPONENT = Path(__file__).parents[1] / "custom_components" / "wiserlink_mpi"
PACKAGE = "wiserlink_mpi_meter_tests"

pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = pkg

for module_name in ("const", "meter"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}", COMPONENT / f"{module_name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PACKAGE}.{module_name}"] = module
    spec.loader.exec_module(module)

meter = sys.modules[f"{PACKAGE}.meter"]


def volume(kind: str, value: float) -> dict:
    return {
        "Type": kind,
        "Power": 0,
        "PowerValidity": False,
        "Unit_Power": "",
        "EnergyConsumed": value,
        "EnergyValidity": True,
        "Unit_Energy": "m3",
        "Name": "",
    }


class StableMeterIdentityTests(unittest.TestCase):
    def test_gas_and_water_have_distinct_semantic_ids(self) -> None:
        self.assertEqual(meter.meter_identity(volume("Gas Meter", 12000)), "gas_meter")
        self.assertEqual(
            meter.meter_identity(volume("Cold Water Meter", 527.2)),
            "cold_water_meter",
        )

    def test_water_identity_survives_gas_disappearing_before_it(self) -> None:
        before = [
            {"Type": "Electricity Meter", "Unit_Energy": "kWh"},
            volume("Gas Meter", 12000),
            volume("Cold Water Meter", 527.2),
        ]
        after = [
            {"Type": "Electricity Meter", "Unit_Energy": "kWh"},
            volume("Cold Water Meter", 527.2),
        ]

        before_found = meter.find_meter_by_identity(before, "cold_water_meter")
        after_found = meter.find_meter_by_identity(after, "cold_water_meter")
        self.assertIsNotNone(before_found)
        self.assertIsNotNone(after_found)
        self.assertEqual(before_found[0], 2)
        self.assertEqual(after_found[0], 1)
        self.assertEqual(meter.meter_identity(after_found[1]), "cold_water_meter")
        self.assertIsNone(meter.find_meter_by_identity(after, "gas_meter"))

    def test_old_numeric_gas_option_cannot_disable_or_rename_water(self) -> None:
        water = volume("Cold Water Meter", 527.2)
        settings = {
            "meter_enabled_10": False,
            "load_name_10": "Gaz",
            "meter_unit_10": "kwh",
        }
        self.assertTrue(meter.meter_enabled(settings, water, [water]))
        self.assertEqual(meter.meter_name(settings, water), "Eau froide")
        self.assertEqual(meter.meter_effective_unit(settings, water), "m3")

    def test_stable_water_options_follow_water_independent_of_index(self) -> None:
        water = volume("Cold Water Meter", 527.2)
        settings = {
            "meter_enabled_cold_water_meter": False,
            "load_name_cold_water_meter": "Compteur eau jardin",
            "meter_unit_cold_water_meter": "m3",
        }
        self.assertFalse(meter.meter_enabled(settings, water, [water]))
        self.assertEqual(meter.meter_name(settings, water), "Compteur eau jardin")
        self.assertEqual(meter.meter_effective_unit(settings, water), "m3")

    def test_load_identity_does_not_shift_when_load3_is_missing(self) -> None:
        meters = [
            {"Type": "Load1", "Unit_Energy": "kWh"},
            {"Type": "Load2", "Unit_Energy": "kWh"},
            {"Type": "Load4", "Unit_Energy": "kWh"},
            {"Type": "Load5", "Unit_Energy": "kWh"},
        ]
        found = meter.find_meter_by_identity(meters, "load4")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], 2)
        self.assertEqual(meter.meter_identity(found[1]), "load4")


if __name__ == "__main__":
    unittest.main()
