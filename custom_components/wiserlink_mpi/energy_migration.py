"""Repair stale WiserLink gas sources in the Home Assistant Energy dashboard."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.energy.data import async_get_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_LEGACY_GAS_SOURCE = "sensor.wiser_energy_gaz_conso"


def _entry_prefix(entry: ConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def _is_stale_wiserlink_gas_source(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    entity_id: str,
) -> bool:
    """Return True only for a gas source that can safely be attributed here."""
    if entity_id == _LEGACY_GAS_SOURCE:
        return True

    registered = registry.async_get(entity_id)
    if registered is not None:
        return registered.config_entry_id == entry.entry_id

    # A broken migration may already have removed the registry entry while the
    # Energy dashboard still contains its old generated entity_id. Restrict this
    # fallback to unmistakable WiserLink gas ids.
    lowered = entity_id.lower()
    return (
        lowered.startswith("sensor.")
        and "_wiserlink_mpi_" in lowered
        and ("_gaz_" in lowered or "_gas_" in lowered)
        and lowered.endswith("_volume")
    )


async def async_repair_energy_gas_source(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Keep the historical Energy gas entity id when this is unambiguous.

    The semantic meter migration can recreate a gas entity under a new generated
    entity_id (for example ``..._gaz_chauffage_volume``) while Energy still points
    to the historical ``..._gaz_volume`` id. When the historical id is now free,
    rename the current canonical WiserLink gas registry entry back to that id so
    recorder/statistics continuity and the Energy configuration are preserved.

    If the historical id is still occupied, do not rename anything: only update
    the Energy source to the canonical entity.
    """
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    canonical_unique_id = f"{prefix}_gas_meter_energyconsumed"
    current_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, canonical_unique_id
    )
    if current_entity_id is None:
        return

    manager = await async_get_manager(hass)
    if manager.data is None:
        return

    energy_sources = manager.data.get("energy_sources", [])
    stale_sources: list[str] = []
    for source in energy_sources:
        if source.get("type") != "gas":
            continue
        source_entity = source.get("stat_energy_from")
        if not isinstance(source_entity, str) or source_entity == current_entity_id:
            continue
        if _is_stale_wiserlink_gas_source(registry, entry, source_entity):
            stale_sources.append(source_entity)

    # Prefer preserving the user's historical WiserLink entity id when exactly
    # one stale source exists and that id is genuinely free in the registry.
    if len(stale_sources) == 1:
        historical_id = stale_sources[0]
        if (
            historical_id != _LEGACY_GAS_SOURCE
            and registry.async_get(historical_id) is None
        ):
            _LOGGER.warning(
                "Restauration de l'entity_id gaz WiserLink historique %s à la place de %s",
                historical_id,
                current_entity_id,
            )
            registry.async_update_entity(
                current_entity_id,
                new_entity_id=historical_id,
            )
            current_entity_id = historical_id

    changed = False
    repaired_sources: list[dict[str, Any]] = []
    for source in energy_sources:
        updated = source.copy()
        if updated.get("type") == "gas":
            source_entity = updated.get("stat_energy_from")
            if (
                isinstance(source_entity, str)
                and source_entity != current_entity_id
                and _is_stale_wiserlink_gas_source(registry, entry, source_entity)
            ):
                updated["stat_energy_from"] = current_entity_id
                changed = True
        repaired_sources.append(updated)

    if changed:
        _LOGGER.warning(
            "Source gaz du tableau Énergie réparée vers %s",
            current_entity_id,
        )
        await manager.async_update({"energy_sources": repaired_sources})
