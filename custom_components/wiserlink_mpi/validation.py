"""Validation helpers for WiserLink UsageMeter snapshots."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

SIGNED_32_MAX = Decimal(2**31 - 1)
_THOUSAND = Decimal("1000")
_MIN_ENERGY_JUMP_WH = Decimal("5000")
_ENERGY_JUMP_FACTOR = Decimal("20")
_SECONDS_PER_HOUR = Decimal("3600")


class UsageMeterValidationError(ValueError):
    """Raised when a UsageMeter snapshot contains unsafe measurements."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("³", "3").replace(" ", "")


def _numeric(value: Any, field: str, index: int) -> Decimal:
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise UsageMeterValidationError(
            f"Valeur {field} invalide pour la voie {index + 1}"
        ) from err
    if not numeric.is_finite():
        raise UsageMeterValidationError(
            f"Valeur {field} non finie pour la voie {index + 1}"
        )
    return numeric


def _optional_numeric(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return numeric if numeric.is_finite() else None


def _base_value(meter: dict[str, Any], field: str, value: Decimal) -> Decimal:
    """Normalize values to the smallest unit normally exposed by the MPI."""
    if field == "Power":
        unit = _text(meter.get("Unit_Power"))
        return value * _THOUSAND if unit == "kw" else value

    unit = _text(meter.get("Unit_Energy"))
    if unit in {"wh", "l", "litre", "liter", "dm3"}:
        return value
    if unit in {"m3", "m^3"}:
        return value * _THOUSAND

    # The integration already treats an unknown electrical unit as kWh.
    return value * _THOUSAND


def _is_volume(meter: dict[str, Any]) -> bool:
    return _text(meter.get("Unit_Energy")) in {
        "m3",
        "m^3",
        "l",
        "litre",
        "liter",
        "dm3",
    }


def _field_is_valid(meter: dict[str, Any], field: str) -> bool:
    validity_field = "PowerValidity" if field == "Power" else "EnergyValidity"
    validity = meter.get(validity_field)
    if validity in (None, ""):
        return True
    return _text(validity) not in {"0", "false", "off", "no"}


def _meter_signature(meter: dict[str, Any]) -> tuple[str, ...]:
    """Return stable API metadata used to detect partial/reordered snapshots."""
    return (
        _text(meter.get("Id")),
        _text(meter.get("Type")),
        _text(meter.get("Unit_Power")),
        _text(meter.get("Unit_Energy")),
    )


def _power_w(meter: dict[str, Any]) -> Decimal | None:
    if not _field_is_valid(meter, "Power"):
        return None
    numeric = _optional_numeric(meter.get("Power"))
    if numeric is None:
        return None
    return abs(_base_value(meter, "Power", numeric))


def _reject_32bit_corruption(
    meter: dict[str, Any], field: str, value: Decimal, index: int
) -> None:
    """Reject values carrying the signed 32-bit overflow/error bit pattern.

    Real-world WiserLink failures have been observed around 0x80000000 after
    conversion to Wh (for example 2 147 483.75 kWh). 0x7fffffff is rejected as
    well so both sides of the signed 32-bit boundary are covered. The check is
    absolute and therefore also protects the very first read after an HA/MPI
    restart, when no previous good sample exists yet.
    """
    base_value = abs(_base_value(meter, field, value))
    if base_value >= SIGNED_32_MAX:
        unit_field = "Unit_Power" if field == "Power" else "Unit_Energy"
        unit = meter.get(unit_field) or "unité inconnue"
        raise UsageMeterValidationError(
            f"Valeur {field} 32 bits invalide pour la voie {index + 1}: "
            f"{value} {unit}"
        )


def validate_usage_meters(meters: list[Any]) -> None:
    """Reject a corrupted snapshot before it can reach Home Assistant."""
    if not meters:
        raise UsageMeterValidationError("La réponse UsageMeter ne contient aucune voie")

    valid_value_found = False
    for index, meter in enumerate(meters):
        if not isinstance(meter, dict):
            raise UsageMeterValidationError(f"Voie {index + 1} invalide")

        for field in ("Power", "EnergyConsumed"):
            value = meter.get(field)
            if value is None:
                continue

            numeric = _numeric(value, field, index)
            if field == "EnergyConsumed" and numeric < 0:
                raise UsageMeterValidationError(
                    f"Index d’énergie négatif pour la voie {index + 1}"
                )

            _reject_32bit_corruption(meter, field, numeric, index)
            valid_value_found = True

    if not valid_value_found:
        raise UsageMeterValidationError(
            "La réponse UsageMeter ne contient aucune mesure"
        )


def snapshot_anomalies(
    previous: list[Any],
    current: list[Any],
    elapsed_seconds: float,
) -> tuple[str, ...]:
    """Return continuity anomalies that justify a confirmation read.

    Static corruption is rejected by ``validate_usage_meters`` first. This
    second layer looks for transient boot/restart artefacts: partial/reordered
    snapshots, cumulative counters going backwards, and implausibly large
    electrical-energy jumps. An anomaly is not discarded on its own; the
    coordinator asks the MPI for a confirmation sample before deciding.
    """
    if len(previous) != len(current):
        return ("structure:count",)

    anomalies: list[str] = []
    elapsed = Decimal(str(max(elapsed_seconds, 0.0)))

    for index, (old_meter, new_meter) in enumerate(zip(previous, current)):
        if not isinstance(old_meter, dict) or not isinstance(new_meter, dict):
            anomalies.append(f"structure:{index + 1}")
            continue

        if _meter_signature(old_meter) != _meter_signature(new_meter):
            anomalies.append(f"structure:{index + 1}")
            continue

        if not (
            _field_is_valid(old_meter, "EnergyConsumed")
            and _field_is_valid(new_meter, "EnergyConsumed")
        ):
            continue

        old_energy = _optional_numeric(old_meter.get("EnergyConsumed"))
        new_energy = _optional_numeric(new_meter.get("EnergyConsumed"))
        if old_energy is None or new_energy is None:
            continue

        old_base = _base_value(old_meter, "EnergyConsumed", old_energy)
        new_base = _base_value(new_meter, "EnergyConsumed", new_energy)
        delta = new_base - old_base

        if delta < 0:
            anomalies.append(f"energy_decrease:{index + 1}")
            continue

        if delta == 0 or _is_volume(new_meter):
            continue

        powers = [
            power
            for power in (_power_w(old_meter), _power_w(new_meter))
            if power is not None
        ]
        if not powers:
            continue

        expected_wh = max(powers) * elapsed / _SECONDS_PER_HOUR
        allowed_jump = max(
            _MIN_ENERGY_JUMP_WH,
            expected_wh * _ENERGY_JUMP_FACTOR,
        )
        if delta > allowed_jump:
            anomalies.append(f"energy_jump:{index + 1}")

    return tuple(anomalies)
