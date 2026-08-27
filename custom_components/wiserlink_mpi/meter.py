"""Helpers for identifying meters returned by the WiserLink UsageMeter API."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .const import (
    CONF_ENABLE_GAS,
    CONF_ENABLE_WATER,
    CONF_LOAD_NAME_PREFIX,
    CONF_METER_ENABLED_PREFIX,
    CONF_METER_UNIT_PREFIX,
    METER_UNIT_AUTO,
    METER_UNIT_KWH,
    METER_UNIT_M3,
    METER_UNIT_WH,
)

_LOAD_RE = re.compile(r"^load\s*([1-5])$", re.IGNORECASE)
_USAGE_TYPES = {"heating", "cooling", "hot water", "sockets"}
_VALID_UNIT_OVERRIDES = {METER_UNIT_AUTO, METER_UNIT_KWH, METER_UNIT_WH, METER_UNIT_M3}
_KNOWN_TYPE_KEYS = {
    "heating": "heating",
    "cooling": "cooling",
    "hot water": "hot_water",
    "sockets": "sockets",
    "others": "others",
    "electricity meter": "electricity_meter",
    "gas meter": "gas_meter",
    "cold water meter": "cold_water_meter",
    "hot water meter": "hot_water_meter",
    "water meter": "water_meter",
    "calorimeter": "calorimeter",
}


def _text(value: Any) -> str:
    """Return a compact string for an API value."""
    return str(value).strip() if value is not None else ""


def _slug(value: str) -> str:
    """Return a compact stable-ish key for an unknown API type."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown_meter"


def meter_type(meter: Mapping[str, Any]) -> str:
    """Return the normalized API meter type."""
    return _text(meter.get("Type"))


def meter_api_name(meter: Mapping[str, Any]) -> str:
    """Return the configured API name, if any."""
    return _text(meter.get("Name"))


def normalized_energy_unit(meter: Mapping[str, Any]) -> str:
    """Normalize the API energy/volume unit for comparisons."""
    return _text(meter.get("Unit_Energy")).lower().replace("³", "3").replace(" ", "")


def normalized_power_unit(meter: Mapping[str, Any]) -> str:
    """Normalize the API power unit for comparisons."""
    return _text(meter.get("Unit_Power")).lower().replace(" ", "")


def load_number(meter: Mapping[str, Any]) -> int | None:
    """Return the physical CT number when the API exposes Load1..Load5."""
    match = _LOAD_RE.fullmatch(meter_type(meter))
    return int(match.group(1)) if match else None


def is_volume_meter(meter: Mapping[str, Any]) -> bool:
    """Return whether EnergyConsumed is a volume rather than electrical energy."""
    return normalized_energy_unit(meter) in {"m3", "m^3"}


def is_gas_meter(meter: Mapping[str, Any]) -> bool:
    """Identify gas semantically, without relying on a list index."""
    combined = f"{meter_type(meter)} {meter_api_name(meter)}".lower()
    if "gas" in combined:
        return True
    return is_volume_meter(meter) and meter_type(meter).lower() == "heating"


def is_water_meter(meter: Mapping[str, Any]) -> bool:
    """Identify water semantically, without relying on a list index."""
    combined = f"{meter_type(meter)} {meter_api_name(meter)}".lower()
    return is_volume_meter(meter) and "water" in combined


def meter_identity(meter: Mapping[str, Any]) -> str:
    """Return the stable semantic identity used for entities and options.

    The Wiser may omit one UsageMeter entry after a reboot. Therefore the list
    position is never an identity: Load4 stays ``load4`` even when Load3 is
    absent, and a Cold Water Meter can never take over the former Gas Meter
    entity merely because it moved to the same array index.
    """
    if number := load_number(meter):
        return f"load{number}"

    kind = meter_type(meter).lower()
    if is_gas_meter(meter):
        return "gas_meter"
    if is_water_meter(meter):
        if "hot" in kind:
            return "hot_water_meter"
        if "cold" in kind:
            return "cold_water_meter"
        return "water_meter"
    if kind in _KNOWN_TYPE_KEYS:
        return _KNOWN_TYPE_KEYS[kind]

    # Unknown firmwares/types are still bound to their API type instead of the
    # volatile list position. Name is only used when the type itself is empty.
    return _slug(meter_type(meter) or meter_api_name(meter))


def meter_option_key(prefix: str, meter: Mapping[str, Any]) -> str:
    """Return one stable per-meter option key."""
    return f"{prefix}{meter_identity(meter)}"


def find_meter_by_identity(
    meters: Sequence[Mapping[str, Any]], identity: str
) -> tuple[int, Mapping[str, Any]] | None:
    """Find a meter and its current API index from its stable identity."""
    for index, meter in enumerate(meters):
        if meter_identity(meter) == identity:
            return index, meter
    return None


def is_others_meter(meter: Mapping[str, Any]) -> bool:
    """Return whether this is the calculated Others entry."""
    return meter_type(meter).lower() == "others"


def is_electricity_meter(meter: Mapping[str, Any]) -> bool:
    """Return whether this is the main electricity/TIC entry."""
    return meter_type(meter).lower() == "electricity meter"


def has_individual_cts(meters: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether all five physical CT entries are present."""
    detected = {number for meter in meters if (number := load_number(meter)) is not None}
    return detected == {1, 2, 3, 4, 5}


def meter_default_enabled(
    meter: Mapping[str, Any], meters: Sequence[Mapping[str, Any]]
) -> bool:
    """Choose safe defaults while avoiding duplicate RT2012 aggregate sensors."""
    if load_number(meter) is not None:
        return True
    if is_volume_meter(meter) or is_others_meter(meter) or is_electricity_meter(meter):
        return True
    if meter_type(meter).lower() in _USAGE_TYPES and has_individual_cts(meters):
        return False
    return True


def meter_enabled(
    settings: Mapping[str, Any],
    meter: Mapping[str, Any],
    meters: Sequence[Mapping[str, Any]],
) -> bool:
    """Return the configured enabled state using only stable per-meter keys."""
    key = meter_option_key(CONF_METER_ENABLED_PREFIX, meter)
    if key in settings:
        return bool(settings[key])

    # Old releases exposed global gas/water booleans. Keep an explicit old True,
    # but never reuse the volatile numeric per-meter keys here: after a missing
    # UsageMeter entry they may belong to a completely different physical meter.
    if is_gas_meter(meter) and settings.get(CONF_ENABLE_GAS) is True:
        return True
    if is_water_meter(meter) and settings.get(CONF_ENABLE_WATER) is True:
        return True

    return meter_default_enabled(meter, meters)


def meter_default_name(meter: Mapping[str, Any]) -> str:
    """Return a useful French name, preferring the name configured in the MPI."""
    if name := meter_api_name(meter):
        return name

    if ct := load_number(meter):
        return f"CT{ct}"

    kind = meter_type(meter).lower()
    if is_gas_meter(meter):
        return "Gaz chauffage" if kind == "heating" else "Gaz"
    if is_water_meter(meter):
        return "Eau chaude" if "hot" in kind else "Eau froide"

    names = {
        "heating": "Chauffage",
        "cooling": "Climatisation",
        "hot water": "Eau chaude",
        "sockets": "Prises",
        "others": "Autres",
        "electricity meter": "Compteur électrique",
        "cold water meter": "Eau froide",
        "hot water meter": "Eau chaude",
        "gas meter": "Gaz",
        "calorimeter": "Calorimètre",
    }
    return names.get(kind, meter_type(meter) or meter_identity(meter))


def meter_name(settings: Mapping[str, Any], meter: Mapping[str, Any]) -> str:
    """Return a stable user override or the detected/API meter name."""
    custom = _text(settings.get(meter_option_key(CONF_LOAD_NAME_PREFIX, meter)))
    return custom or meter_default_name(meter)


def meter_unit_override(
    settings: Mapping[str, Any], meter: Mapping[str, Any]
) -> str:
    """Return a validated stable per-meter unit override."""
    value = _text(settings.get(meter_option_key(CONF_METER_UNIT_PREFIX, meter))).lower()
    return value if value in _VALID_UNIT_OVERRIDES else METER_UNIT_AUTO


def meter_effective_unit(
    settings: Mapping[str, Any], meter: Mapping[str, Any]
) -> str:
    """Return the configured unit or the unit reported by the MPI in Auto mode."""
    override = meter_unit_override(settings, meter)
    if override != METER_UNIT_AUTO:
        return override

    api_unit = normalized_energy_unit(meter)
    if api_unit in {"m3", "m^3"}:
        return METER_UNIT_M3
    if api_unit == "wh":
        return METER_UNIT_WH
    return METER_UNIT_KWH
