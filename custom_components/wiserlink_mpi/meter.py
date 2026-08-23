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
)

_LOAD_RE = re.compile(r"^load\s*([1-5])$", re.IGNORECASE)
_USAGE_TYPES = {"heating", "cooling", "hot water", "sockets"}


def _text(value: Any) -> str:
    """Return a compact string for an API value."""
    return str(value).strip() if value is not None else ""


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
    index: int,
    meter: Mapping[str, Any],
    meters: Sequence[Mapping[str, Any]],
) -> bool:
    """Return the configured enabled state with compatibility for old options."""
    key = f"{CONF_METER_ENABLED_PREFIX}{index}"
    if key in settings:
        return bool(settings[key])

    # Old versions exposed global gas/water booleans. Keep an explicit old True,
    # but never let the previous default False hide a newly detected real meter.
    if is_gas_meter(meter) and settings.get(CONF_ENABLE_GAS) is True:
        return True
    if is_water_meter(meter) and settings.get(CONF_ENABLE_WATER) is True:
        return True

    return meter_default_enabled(meter, meters)


def meter_default_name(meter: Mapping[str, Any], index: int) -> str:
    """Return a useful French name, preferring the name configured in the MPI."""
    if name := meter_api_name(meter):
        return name

    if ct := load_number(meter):
        return f"CT{ct}"

    kind = meter_type(meter).lower()
    if is_gas_meter(meter):
        return "Gaz chauffage" if kind == "heating" else "Gaz"
    if is_water_meter(meter):
        return "Eau chaude" if "hot" in kind else "Eau"

    names = {
        "heating": "Chauffage",
        "cooling": "Climatisation",
        "hot water": "Eau chaude",
        "sockets": "Prises",
        "others": "Autres",
        "electricity meter": "Compteur électrique",
        "cold water meter": "Eau froide",
        "hot water meter": "Eau chaude",
        "calorimeter": "Calorimètre",
    }
    return names.get(kind, meter_type(meter) or f"Voie {index + 1}")


def _legacy_default_name(index: int) -> str | None:
    """Return the automatic name stored by releases <= 0.7.1."""
    if 0 <= index <= 9:
        return f"Voie {index + 1}"
    if index == 10:
        return "Module gaz"
    if index == 11:
        return "Module eau"
    return None


def meter_name(
    settings: Mapping[str, Any], meter: Mapping[str, Any], index: int
) -> str:
    """Return a real user override or the detected/default meter name."""
    custom = _text(settings.get(f"{CONF_LOAD_NAME_PREFIX}{index}"))
    if custom and custom != _legacy_default_name(index):
        return custom
    return meter_default_name(meter, index)
