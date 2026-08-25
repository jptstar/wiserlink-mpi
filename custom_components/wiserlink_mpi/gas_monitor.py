"""Helpers for protecting and monitoring the daily MPR gas reading."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_MINUTES_PER_DAY = 24 * 60


def decimal_value(value: Any) -> Decimal | None:
    """Return a finite Decimal or None."""
    if value is None:
        return None
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return numeric if numeric.is_finite() else None


def clock_minutes(value: str | time) -> int:
    """Convert HH:MM[:SS] or datetime.time to minutes since midnight."""
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    parts = str(value).split(":")
    if len(parts) < 2:
        raise ValueError(f"Heure invalide: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Heure invalide: {value}")
    return hour * 60 + minute


def circular_drift_minutes(observed: datetime, target: str | time) -> int:
    """Return signed drift from target, correctly handling midnight."""
    observed_minutes = observed.hour * 60 + observed.minute
    target_minutes = clock_minutes(target)
    return int((observed_minutes - target_minutes + 720) % _MINUTES_PER_DAY - 720)


def next_estimated_reading(last_detected: datetime | None) -> datetime | None:
    """Estimate the next autonomous MPE transmission at +24 hours."""
    return last_detected + timedelta(hours=24) if last_detected else None


def should_correct_drift(
    *,
    now: datetime,
    last_detected: datetime | None,
    target_time: str | time,
    tolerance_minutes: int,
    control_time: str | time,
) -> tuple[bool, str | None]:
    """Decide whether a single pre-midnight corrective reboot is justified.

    A reboot is requested only after the configured control time and only when
    there is actual evidence of drift: a reading detected today outside the
    tolerance window, or no detected reading for more than 24h+tolerance.
    With no learned reading time, no automatic reboot is requested.
    """
    if now.hour * 60 + now.minute < clock_minutes(control_time):
        return False, None
    if last_detected is None:
        return False, None

    if last_detected.tzinfo is not None and now.tzinfo is not None:
        observed = last_detected.astimezone(now.tzinfo)
    else:
        observed = last_detected

    drift = circular_drift_minutes(observed, target_time)
    if observed.date() == now.date():
        if abs(drift) > tolerance_minutes:
            return True, f"dérive de relève gaz {drift:+d} min"
        return False, None

    age = now - observed
    if age > timedelta(hours=24, minutes=tolerance_minutes):
        return True, f"aucune relève gaz détectée depuis {age.total_seconds() / 3600:.1f} h"
    return False, None


def protected_cumulative_value(
    raw_value: Decimal | None, last_valid_value: Decimal | None
) -> tuple[Decimal | None, bool]:
    """Protect a cumulative gas index from a transient reset or rollback."""
    if raw_value is None:
        return last_valid_value, last_valid_value is not None
    if last_valid_value is None:
        return raw_value, False
    if raw_value < last_valid_value:
        return last_valid_value, True
    return raw_value, False
