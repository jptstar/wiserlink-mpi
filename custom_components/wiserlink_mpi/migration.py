"""One-time migration from volatile UsageMeter indexes to stable identities."""

from __future__ import annotations

import re
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
from .meter import is_gas_meter, is_water_meter, meter_api_name, meter_identity

_LEGACY_SENSOR_UNIQUE_RE = re.compile(
    r"^(?P<prefix>.+)_(?P<index>\d+)_(?P<metric>power|energyconsumed)$"
)


def _entry_prefix(entry: ConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def _registry_text(item: Any) -> str:
    return " ".join(
        str(value or "")
        for value in (
            getattr(item, "entity_id", None),
            getattr(item, "name", None),
            getattr(item, "original_name", None),
        )
    ).lower()


def _registry_device_class(item: Any) -> str:
    value = getattr(item, "device_class", None) or getattr(
        item, "original_device_class", None
    )
    return str(value or "").lower()


def _infer_identity(item: Any, meters: list[dict[str, Any]]) -> str | None:
    """Infer a legacy entity identity without trusting its old list index."""
    text = _registry_text(item)
    device_class = _registry_device_class(item)

    if device_class == "gas" or re.search(r"\b(?:gaz|gas)\b", text):
        return "gas_meter"

    if device_class == "water":
        water_ids = {meter_identity(meter) for meter in meters if is_water_meter(meter)}
        if "chaud" in text or "hot" in text:
            return "hot_water_meter"
        if "froid" in text or "cold" in text:
            return "cold_water_meter"
        if len(water_ids) == 1:
            return next(iter(water_ids))
        return "water_meter"

    # Preserve user/API names such as Pool House Ext, Domotique, Cuisine, etc.
    for meter in meters:
        api_name = meter_api_name(meter).strip().lower()
        if api_name and api_name in text:
            return meter_identity(meter)

    for number in range(1, 6):
        if re.search(rf"\b(?:ct|load)\s*{number}\b", text):
            return f"load{number}"

    semantic_words = (
        (
            (
                "compteur électrique",
                "electricity meter",
                "teleinfo",
                "téléinfo",
                " tic ",
            ),
            "electricity_meter",
        ),
        (("autres", "others"), "others"),
        (("climatisation", "cooling"), "cooling"),
        (("prises", "sockets"), "sockets"),
        (("chauffage", "heating"), "heating"),
        (("eau chaude", "hot water"), "hot_water"),
    )
    for words, identity in semantic_words:
        if any(word in text for word in words):
            return identity
    return None


def _fake_named_option(value: Any) -> Any:
    text = str(value or "")
    return type(
        "LegacyOption",
        (),
        {
            "entity_id": text,
            "name": text,
            "original_name": text,
            "device_class": None,
            "original_device_class": None,
        },
    )()


def migrate_meter_identities(
    hass: HomeAssistant, entry: ConfigEntry, meters: list[dict[str, Any]]
) -> None:
    """Repair old numeric entities and prefer their history over v0.8.17 duplicates.

    The old integration used the position in UsageMeterList as unique_id. A missing
    Load3 or MPR entry shifts every following position. This migration derives the
    semantic identity from entity metadata/API names and, when exactly one CT is
    missing, by elimination. Existing user entity_ids and long-term statistics are
    kept; any short-lived semantic duplicate created by 0.8.17 is removed first.
    """
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    entries = list(er.async_entries_for_config_entry(registry, entry.entry_id))

    groups: dict[int, list[tuple[Any, str]]] = {}
    for item in entries:
        if item.platform != DOMAIN or item.domain != "sensor":
            continue
        match = _LEGACY_SENSOR_UNIQUE_RE.fullmatch(item.unique_id or "")
        if match is None or match.group("prefix") != prefix:
            continue
        groups.setdefault(int(match.group("index")), []).append(
            (item, match.group("metric"))
        )

    if not groups:
        return

    index_to_identity: dict[int, str] = {}

    # First pass: registry metadata and current API names.
    for index, group in groups.items():
        identities = {
            identity
            for item, _metric in group
            if (identity := _infer_identity(item, meters)) is not None
        }
        if len(identities) == 1:
            index_to_identity[index] = next(iter(identities))

    # Old option names can recover disabled/custom-named entities too.
    for index in groups:
        value = entry.options.get(f"{CONF_LOAD_NAME_PREFIX}{index}")
        if not value:
            continue
        identity = _infer_identity(_fake_named_option(value), meters)
        if identity is not None:
            index_to_identity.setdefault(index, identity)

    # If four CT identities are known and one old CT slot remains unresolved,
    # the remaining identity is unambiguous. This repairs e.g. Chargeurs Voitures
    # when Load3 is temporarily absent while Load1,2,4,5 are still present.
    mapped_loads = {
        identity for identity in index_to_identity.values() if identity.startswith("load")
    }
    missing_loads = {f"load{number}" for number in range(1, 6)} - mapped_loads
    unresolved_ct_slots = [
        index for index in sorted(groups) if index < 5 and index not in index_to_identity
    ]
    if len(missing_loads) == 1 and len(unresolved_ct_slots) == 1:
        index_to_identity[unresolved_ct_slots[0]] = next(iter(missing_loads))

    if not index_to_identity:
        return

    # Preserve the legacy entity (and therefore its entity_id/statistics). If
    # v0.8.17 already created a semantic duplicate, remove only that duplicate.
    for index, identity in index_to_identity.items():
        for item, metric in groups.get(index, []):
            new_unique_id = f"{prefix}_{identity}_{metric}"
            existing_entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, new_unique_id
            )
            if existing_entity_id and existing_entity_id != item.entity_id:
                existing = registry.async_get(existing_entity_id)
                if existing is not None and existing.config_entry_id == entry.entry_id:
                    registry.async_remove(existing_entity_id)
            registry.async_update_entity(item.entity_id, new_unique_id=new_unique_id)

    # Move old per-index options to the stable key and remove the dangerous
    # numeric copies so a later list shift can never reassign them again.
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

    if changed:
        hass.config_entries.async_update_entry(entry, options=options)
