"""Regression tests for WiserLink entity registry repair planning."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest

COMPONENT = Path(__file__).parents[1] / "custom_components" / "wiserlink_mpi"
PACKAGE = "wiserlink_migration_tests"

pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = pkg

for module_name in ("const", "meter", "migration_model"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}", COMPONENT / f"{module_name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PACKAGE}.{module_name}"] = module
    spec.loader.exec_module(module)

model = sys.modules[f"{PACKAGE}.migration_model"]

PREFIX = "wiser123"
OLD = datetime(2025, 1, 1, tzinfo=timezone.utc)
NEW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def meter(kind: str, name: str = "", unit: str = "kWh") -> dict:
    return {
        "Type": kind,
        "Name": name,
        "Unit_Energy": unit,
        "Unit_Power": "" if unit == "m3" else "W",
    }


def entry(
    entity_id: str,
    unique_id: str,
    original_name: str,
    *,
    created_at: datetime,
    device_class: str | None = None,
) -> dict:
    return {
        "entity_id": entity_id,
        "unique_id": unique_id,
        "name": None,
        "original_name": original_name,
        "device_class": device_class,
        "original_device_class": device_class,
        "created_at": created_at,
    }


class MigrationRepairTests(unittest.TestCase):
    def test_teleinfo_keeps_historical_entity_and_removes_new_duplicate(self) -> None:
        meters = [meter("Electricity Meter")]
        entries = [
            entry(
                "sensor.teleinfo_conso_total_energie",
                f"{PREFIX}_10_energyconsumed",
                "Teleinfo Conso Total Énergie",
                created_at=OLD,
                device_class="energy",
            ),
            entry(
                "sensor.compteur_electrique_energie",
                f"{PREFIX}_electricity_meter_energyconsumed",
                "Compteur électrique Énergie",
                created_at=NEW,
                device_class="energy",
            ),
        ]

        plans, _ = model.build_repair_plan(PREFIX, entries, meters)
        plan = next(item for item in plans if item["identity"] == "electricity_meter")
        self.assertEqual(plan["survivor_entity_id"], "sensor.teleinfo_conso_total_energie")
        self.assertEqual(
            plan["canonical_unique_id"],
            f"{PREFIX}_electricity_meter_energyconsumed",
        )
        self.assertEqual(
            plan["remove_entity_ids"], ["sensor.compteur_electrique_energie"]
        )
        self.assertEqual(plan["suggested_name"], "Teleinfo Conso Total")

    def test_missing_load3_is_recovered_by_elimination_without_shifting_load4(self) -> None:
        meters = [
            meter("Load1", "Pool House Ext"),
            meter("Load2", "Domotique"),
            meter("Load4", "Cuisine"),
            meter("Load5", "Buanderie"),
        ]
        entries = [
            entry(
                "sensor.pool_house_ext_energie",
                f"{PREFIX}_0_energyconsumed",
                "Pool House Ext Énergie",
                created_at=OLD,
            ),
            entry(
                "sensor.domotique_energie",
                f"{PREFIX}_1_energyconsumed",
                "Domotique Énergie",
                created_at=OLD,
            ),
            entry(
                "sensor.chargeurs_voitures_energie",
                f"{PREFIX}_2_energyconsumed",
                "Chargeurs Voitures Énergie",
                created_at=OLD,
            ),
            entry(
                "sensor.cuisine_energie",
                f"{PREFIX}_3_energyconsumed",
                "Cuisine Énergie",
                created_at=OLD,
            ),
            entry(
                "sensor.buanderie_energie",
                f"{PREFIX}_4_energyconsumed",
                "Buanderie Énergie",
                created_at=OLD,
            ),
            entry(
                "sensor.chargeurs_voitures_energie_2",
                f"{PREFIX}_load3_energyconsumed",
                "Chargeurs Voitures Énergie",
                created_at=NEW,
            ),
        ]

        plans, index_map = model.build_repair_plan(PREFIX, entries, meters)
        self.assertEqual(index_map[2], "load3")
        self.assertEqual(index_map[3], "load4")
        self.assertEqual(index_map[4], "load5")

        load3 = next(item for item in plans if item["identity"] == "load3")
        self.assertEqual(load3["survivor_entity_id"], "sensor.chargeurs_voitures_energie")
        self.assertEqual(
            load3["remove_entity_ids"], ["sensor.chargeurs_voitures_energie_2"]
        )

    def test_gas_is_kept_even_when_gas_meter_is_absent_from_api(self) -> None:
        meters = [meter("Cold Water Meter", unit="m3")]
        entries = [
            entry(
                "sensor.gaz_volume",
                f"{PREFIX}_11_energyconsumed",
                "Gaz Volume",
                created_at=OLD,
                device_class="gas",
            )
        ]

        plans, _ = model.build_repair_plan(PREFIX, entries, meters)
        gas = next(item for item in plans if item["identity"] == "gas_meter")
        self.assertEqual(gas["survivor_entity_id"], "sensor.gaz_volume")
        self.assertEqual(
            gas["canonical_unique_id"], f"{PREFIX}_gas_meter_energyconsumed"
        )

    def test_water_does_not_take_gas_identity_when_gas_disappears(self) -> None:
        meters = [meter("Cold Water Meter", unit="m3")]
        entries = [
            entry(
                "sensor.eau_volume",
                f"{PREFIX}_12_energyconsumed",
                "Eau Volume",
                created_at=OLD,
                device_class="water",
            )
        ]

        plans, _ = model.build_repair_plan(PREFIX, entries, meters)
        water = next(item for item in plans if item["identity"] == "cold_water_meter")
        self.assertEqual(water["survivor_entity_id"], "sensor.eau_volume")
        self.assertEqual(
            water["canonical_unique_id"],
            f"{PREFIX}_cold_water_meter_energyconsumed",
        )


if __name__ == "__main__":
    unittest.main()
