"""Repair WiserLink meter registry entries after index-based releases."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_LOAD_NAME_PREFIX,
    CONF_METER_ENABLED_PREFIX,
    CONF_METER_UNIT_PREFIX,
    DOMAIN,
)
from .migration_model import build_repair_plan

_LOGGER = logging.getLogger(__name__)


def _entry_prefix(entry: ConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def _snapshot(item: er.RegistryEntry) -> dict[str, Any]:
    """Convert one registry entry to the pure migration model format."""
    return {
        "entity_id": item.entity_id,
        "unique_id": item.unique_id,
        "name": getattr(item, "name", None),
        "original_name": getattr(item, "original_name", None),
        "device_class": getattr(item, "device_class", None),
        "original_device_class": getattr(item, "original_device_class", None),
        "created_at": getattr(item, "created_at", None),
    }


def migrate_meter_identities(
    hass: HomeAssistant, entry: ConfigEntry, meters: list[dict[str, Any]]
) -> None:
    """Repair meter unique ids while preserving historical entity ids.

    Releases before 0.8.17 used the volatile position in ``UsageMeterList`` as
    the unique id. The first semantic-identity migrations could then leave both
    the historical entity and a newly-created ``_2`` entity in the registry.

    This repair is intentionally one-way and conservative:

    * an existing historical ``entity_id`` is never renamed;
    * the oldest matching entity is kept;
    * short-lived duplicates are removed before assigning the canonical unique id;
    * current Wiser API names and meter types are used as stronger evidence than
      a semantic unique id written by the broken migration;
    * a missing Load3/Gas/Water entry never makes the following API position take
      over its identity.
    """
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)

    registry_entries = [
        item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.platform == DOMAIN and item.domain == "sensor"
    ]
    snapshots = [_snapshot(item) for item in registry_entries]
    plans, index_to_identity = build_repair_plan(prefix, snapshots, meters)

    if not plans and not index_to_identity:
        return

    # Repair registry entries before sensor platform setup. Remove the newer
    # duplicate first so Home Assistant can safely assign its canonical unique id
    # back to the historical entity without changing the historical entity_id.
    for plan in plans:
        survivor_id = plan["survivor_entity_id"]
        canonical_unique_id = plan["canonical_unique_id"]

        for duplicate_id in plan["remove_entity_ids"]:
            duplicate = registry.async_get(duplicate_id)
            if duplicate is None or duplicate.config_entry_id != entry.entry_id:
                continue
            _LOGGER.warning(
                "Suppression du doublon WiserLink %s au profit de l'entité historique %s",
                duplicate_id,
                survivor_id,
            )
            registry.async_remove(duplicate_id)

        survivor = registry.async_get(survivor_id)
        if survivor is None or survivor.config_entry_id != entry.entry_id:
            continue

        # A stale conflicting canonical entry may not have been part of the plan
        # if its metadata was incomplete. It is safe to remove only when it belongs
        # to this same config entry and is not the chosen historical entity.
        conflicting_id = registry.async_get_entity_id(
            "sensor", DOMAIN, canonical_unique_id
        )
        if conflicting_id and conflicting_id != survivor_id:
            conflicting = registry.async_get(conflicting_id)
            if conflicting is not None and conflicting.config_entry_id == entry.entry_id:
                _LOGGER.warning(
                    "Suppression du conflit WiserLink %s avant restauration de %s",
                    conflicting_id,
                    survivor_id,
                )
                registry.async_remove(conflicting_id)

        if survivor.unique_id != canonical_unique_id:
            _LOGGER.info(
                "Migration WiserLink %s: unique_id %s -> %s (entity_id conservé)",
                survivor_id,
                survivor.unique_id,
                canonical_unique_id,
            )
            registry.async_update_entity(
                survivor_id, new_unique_id=canonical_unique_id
            )

    # Move any remaining old numeric configuration keys to the same semantic
    # identity. The historical value wins because it is the setting the user had
    # before the broken migration.
    options = dict(entry.options)
    changed = False
    for index, identity in index_to_identity.items():
        for option_prefix in (
            CONF_METER_ENABLED_PREFIX,
            CONF_LOAD_NAME_PREFIX,
            CONF_METER_UNIT_PREFIX,
        ):
            old_key = f"{option_prefix}{index}"
            if old_key not in options:
                continue
            new_key = f"{option_prefix}{identity}"
            options[new_key] = options[old_key]
            del options[old_key]
            changed = True

    # If a historical entity was kept over a migration-created duplicate, restore
    # the integration-provided historical name in Configurer as well. This repairs
    # names such as "Teleinfo Conso Total" and "Chargeurs Voitures" without ever
    # renaming the Home Assistant entity_id itself.
    historical_names: dict[str, set[str]] = {}
    for plan in plans:
        suggested = plan.get("suggested_name")
        if not suggested:
            continue
        if not (plan.get("survivor_was_legacy") or plan.get("candidate_count", 0) > 1):
            continue
        historical_names.setdefault(plan["identity"], set()).add(str(suggested))

    for identity, names in historical_names.items():
        if len(names) != 1:
            continue
        name_key = f"{CONF_LOAD_NAME_PREFIX}{identity}"
        historical_name = next(iter(names))
        if options.get(name_key) != historical_name:
            options[name_key] = historical_name
            changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, options=options)
