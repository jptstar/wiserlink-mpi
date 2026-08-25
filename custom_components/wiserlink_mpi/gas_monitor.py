"""Helpers for monitoring the daily MPR gas reading drift."""

from __future__ import annotations

from datetime import datetime, time, timedelta

_MINUTES_PER_DAY = 24 * 60


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
    """Decide whether one corrective reboot is justified after control time.

    The integration never invents a reading time. A reboot is requested only
    after the configured control time and only when a previously detected gas
    reading is outside the target tolerance, or has become older than 24 hours
    plus the tolerance. The coordinator enforces a maximum of one automatic
    reboot per local calendar day.
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
