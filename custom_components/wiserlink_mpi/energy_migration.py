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


def _restore_historical_registry_entity(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    canonical_unique_id: str,
    current_entity_id: str,
    historical_entity_id: str,
) -> str:
    """Make the historical entity_id the canonical gas entity when safe.

    The recorder long-term statistic id is derived from the entity id. Therefore
    the historical entity id wins over a migration-created replacement. We remove
    only the recent duplicate and move the canonical unique id onto the historical
    registry entry. No recorder/statistics rows are deleted or rewritten here.
    """
    if historical_entity_id == _LEGACY_GAS_SOURCE:
        return current_entity_id

    historical = registry.async_get(historical_entity_id)
    current = registry.async_get(current_entity_id)

    if historical is not None:
        if historical.config_entry_id != entry.entry_id:
            return current_entity_id
        if current_entity_id != historical_entity_id and current is not None:
            _LOGGER.warning(
                "Suppression du doublon gaz récent %s; conservation de l'entité historique %s",
                current_entity_id,
                historical_entity_id,
            )
            registry.async_remove(current_entity_id)
        historical = registry.async_get(historical_entity_id)
        if historical is not None and historical.unique_id != canonical_unique_id:
            _LOGGER.warning(
                "Rattachement du unique_id gaz canonique à l'entité historique %s",
                historical_entity_id,
            )
            registry.async_update_entity(
                historical_entity_id,
                new_unique_id=canonical_unique_id,
            )
        return historical_entity_id

    # If the historical id is no longer present in the registry, rename the
    # canonical entity back to that id. This restores the recorder statistic_id
    # expected by the Energy dashboard without touching the statistics database.
    if current is not None:
        _LOGGER.warning(
            "Restauration de l'entity_id gaz historique %s à la place de %s",
            historical_entity_id,
            current_entity_id,
        )
        registry.async_update_entity(
            current_entity_id,
            new_entity_id=historical_entity_id,
        )
        return historical_entity_id

    return current_entity_id


async def async_repair_energy_gas_source(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Preserve the historical WiserLink gas entity and Energy source.

    Previous semantic migrations could recreate the gas sensor with a generated
    entity id such as ``..._gaz_chauffage_volume`` while the Energy dashboard and
    recorder history still use ``..._gaz_volume``. The historical entity id is
    authoritative: when it can be identified unambiguously, keep/recreate that
    registry entity and attach the canonical ``gas_meter`` unique id to it.
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

    # Exactly one old WiserLink gas source is an unambiguous historical identity.
    # Keep it as the actual entity id so its recorder/statistics timeline continues
    # under the same statistic_id.
    unique_stale = list(dict.fromkeys(stale_sources))
    if len(unique_stale) == 1:
        current_entity_id = _restore_historical_registry_entity(
            registry,
            entry,
            canonical_unique_id,
            current_entity_id,
            unique_stale[0],
        )

    # Normally the Energy source now already points at the preserved historical
    # entity id. Only rewrite genuinely stale WiserLink gas sources (not arbitrary
    # user entities), notably the very old pre-integration source.
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
            "Source gaz du tableau Énergie réparée vers l'entité historique %s",
            current_entity_id,
        )
        await manager.async_update({"energy_sources": repaired_sources})
