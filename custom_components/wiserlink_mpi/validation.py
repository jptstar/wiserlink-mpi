"""Validation helpers for WiserLink UsageMeter snapshots."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

SIGNED_32_MAX = Decimal(2**31 - 1)
_THOUSAND = Decimal("1000")


class UsageMeterValidationError(ValueError):
    """Raised when a UsageMeter snapshot contains unsafe measurements."""


def _text(value: Any) -> str:
    return str(value).strip().lower().replace("³", "3").replace(" ", "") if value is not None else ""


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
