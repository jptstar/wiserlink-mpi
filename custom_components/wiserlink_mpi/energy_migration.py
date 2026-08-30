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


def _move_canonical_unique_id_to_historical_entity(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    canonical_unique_id: str,
    current_entity_id: str,
    historical_entity_id: str,
) -> tuple[str, bool]:
    """Bind the gas unique id to the historical entity id without renaming it.

    Recorder listens for entity-id rename events and may try to migrate statistics.
    That is exactly what we must avoid here because the historical statistic id
    already exists. Instead we only move unique ids in the entity registry:

    * the migration-created gas entry receives a temporary unique id;
    * the historical entity keeps/is created with its historical entity id;
    * the canonical gas unique id is attached to that historical entity;
    * the recent duplicate is then removed.

    No entity-id rename event is emitted, so existing recorder/statistics rows are
    left untouched. On the following config-entry reload, new gas states are
    written directly under the historical entity/statistic id.
    """
    if historical_entity_id == _LEGACY_GAS_SOURCE:
        return current_entity_id, False

    current = registry.async_get(current_entity_id)
    if current is None or current.config_entry_id != entry.entry_id:
        return current_entity_id, False

    historical = registry.async_get(historical_entity_id)
    if historical is not None and historical.config_entry_id != entry.entry_id:
        return current_entity_id, False

    temp_unique_id = f"{canonical_unique_id}{_TEMP_UNIQUE_SUFFIX}"
    stale_temp_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, temp_unique_id
    )
    if stale_temp_entity_id and stale_temp_entity_id != current_entity_id:
        stale_temp = registry.async_get(stale_temp_entity_id)
        if stale_temp is not None and stale_temp.config_entry_id == entry.entry_id:
            registry.async_remove(stale_temp_entity_id)
        else:
            return current_entity_id, False

    # Free the canonical unique id without changing the recent entity_id.
    if current.unique_id != temp_unique_id:
        registry.async_update_entity(
            current_entity_id,
            new_unique_id=temp_unique_id,
        )

    if historical is not None:
        # The historical registry entity survived. Reattach the canonical unique
        # id to it, preserving its entity_id and therefore its statistics id.
        if historical.unique_id != canonical_unique_id:
            registry.async_update_entity(
                historical_entity_id,
                new_unique_id=canonical_unique_id,
            )
        canonical_entity_id = historical_entity_id
    else:
        # The historical registry entry is gone, but Recorder/Energy still know
        # its entity_id. Create a fresh registry entry directly under that exact
        # object id. This is deliberately not a rename of the recent entity.
        try:
            domain, object_id = historical_entity_id.split(".", 1)
        except ValueError:
            # Put the canonical unique id back before giving up.
            registry.async_update_entity(
                current_entity_id,
                new_unique_id=canonical_unique_id,
            )
            return current_entity_id, False
        if domain != "sensor":
            registry.async_update_entity(
                current_entity_id,
                new_unique_id=canonical_unique_id,
            )
            return current_entity_id, False

        recreated = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            canonical_unique_id,
            capabilities=current.capabilities,
            config_entry=entry,
            device_id=current.device_id,
            disabled_by=current.disabled_by,
            entity_category=current.entity_category,
            has_entity_name=current.has_entity_name,
            hidden_by=current.hidden_by,
            object_id_base=current.object_id_base,
            original_device_class=current.original_device_class,
            original_icon=current.original_icon,
            original_name=current.original_name,
            suggested_object_id=object_id,
            supported_features=current.supported_features,
            translation_key=current.translation_key,
            unit_of_measurement=current.unit_of_measurement,
        )
        canonical_entity_id = recreated.entity_id
        if canonical_entity_id != historical_entity_id:
            # Unexpected collision: remove the new entry and restore the previous
            # canonical mapping. Do not guess or touch Recorder statistics.
            registry.async_remove(canonical_entity_id)
            registry.async_update_entity(
                current_entity_id,
                new_unique_id=canonical_unique_id,
            )
            return current_entity_id, False

    _LOGGER.warning(
        "Restauration sûre de l'entité gaz historique %s; suppression du doublon récent %s",
        canonical_entity_id,
        current_entity_id,
    )
    registry.async_remove(current_entity_id)
    return canonical_entity_id, True


async def async_repair_energy_gas_source(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Preserve the historical WiserLink gas entity and Energy source.

    Return True when the entity registry was rebuilt and the config entry must be
    reloaded so the running entity binds to the historical entity id.
    """
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    canonical_unique_id = f"{prefix}_gas_meter_energyconsumed"
    current_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, canonical_unique_id
    )
    if current_entity_id is None:
        return False

    manager = await async_get_manager(hass)
    if manager.data is None:
        return False

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

    registry_rebuilt = False
    unique_stale = list(dict.fromkeys(stale_sources))
    if len(unique_stale) == 1:
        historical_entity_id = unique_stale[0]
        current_entity_id, registry_rebuilt = (
            _move_canonical_unique_id_to_historical_entity(
                registry,
                entry,
                canonical_unique_id,
                current_entity_id,
                historical_entity_id,
            )
        )

    # If the source is the very old pre-integration gas entity, or preservation of
    # a historical WiserLink id was impossible, point Energy at the canonical
    # entity. When preservation succeeded this normally changes nothing because
    # Energy already points at the historical id.
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

    return registry_rebuilt
