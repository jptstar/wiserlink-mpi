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
_TEMP_UNIQUE_SUFFIX = "__historical_gas_replaced"


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

    lowered = entity_id.lower()
    return (
        lowered.startswith("sensor.")
        and "_wiserlink_mpi_" in lowered
        and ("_gaz_" in lowered or "_gas_" in lowered)
        and lowered.endswith("_volume")
    )


def _undo_wrong_historical_restore(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    canonical_unique_id: str,
) -> tuple[str | None, bool]:
    """Undo the 0.8.23 temporary unique-id swap without touching Recorder.

    0.8.23 could move the entity that already owned the recorder history to a
    temporary unique id and create an empty replacement under the old entity id.
    When that exact temporary marker is present, it is authoritative evidence of
    our own migration. Restore the canonical unique id to that original entity,
    remove only the replacement registry entry, and keep every entity_id and
    recorder/statistics row of the historical entity unchanged.
    """
    temp_unique_id = f"{canonical_unique_id}{_TEMP_UNIQUE_SUFFIX}"
    historical_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, temp_unique_id
    )
    current_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, canonical_unique_id
    )

    if historical_entity_id is None:
        return current_entity_id, False

    historical = registry.async_get(historical_entity_id)
    if historical is None or historical.config_entry_id != entry.entry_id:
        return current_entity_id, False

    if current_entity_id is not None and current_entity_id != historical_entity_id:
        current = registry.async_get(current_entity_id)
        if current is None or current.config_entry_id != entry.entry_id:
            return current_entity_id, False
        _LOGGER.warning(
            "Suppression de l'entité gaz vide créée par migration %s; conservation de %s",
            current_entity_id,
            historical_entity_id,
        )
        registry.async_remove(current_entity_id)

    registry.async_update_entity(
        historical_entity_id,
        new_unique_id=canonical_unique_id,
    )
    _LOGGER.warning(
        "Restauration du unique_id gaz canonique sur l'entité qui possède l'historique: %s",
        historical_entity_id,
    )
    return historical_entity_id, True


async def async_repair_energy_gas_source(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Keep the current/history-owning gas entity and repair Energy only.

    Entity ids are never renamed here. If a previous WiserLink migration left the
    history-owning gas entity under our temporary unique id, undo only that swap.
    Afterwards the Energy dashboard is pointed at the canonical WiserLink gas
    entity. Recorder/statistics data is neither deleted nor rewritten.

    Return True when the registry mapping changed and the config entry should be
    reloaded so the running entity binds to the restored registry entry.
    """
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    canonical_unique_id = f"{prefix}_gas_meter_energyconsumed"

    current_entity_id, registry_changed = _undo_wrong_historical_restore(
        registry, entry, canonical_unique_id
    )
    if current_entity_id is None:
        return registry_changed

    manager = await async_get_manager(hass)
    if manager.data is None:
        return registry_changed

    changed = False
    repaired_sources: list[dict[str, Any]] = []
    for source in manager.data.get("energy_sources", []):
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

    return registry_changed
