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


def clock_string(value: datetime | str | time) -> str:
    """Return a normalized HH:MM:SS clock string."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    parts = str(value).split(":")
    if len(parts) < 2:
        raise ValueError(f"Heure invalide: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError(f"Heure invalide: {value}")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


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
    reference_time: str | time | None = None,
) -> tuple[bool, str | None]:
    """Decide whether one early corrective reboot is justified.

    ``control_time`` opens the correction window and ``target_time`` closes it.
    A reboot is therefore never initiated at or after the desired reading time.
    The decision is based on the clock time of the latest real gas reading, not
    on the mere absence of today's reading. This prevents a normal 23:47 cycle
    from being rebooted at 23:35 just because today's packet has not arrived yet.

    After a successful correction, ``reference_time`` can be the learned cycle
    time (for example 23:47). Future drift is then measured from that stable
    cycle instead of forcing an exact 23:45 transmission every day.
    """
    if last_detected is None:
        return False, None

    now_minutes = now.hour * 60 + now.minute
    control_minutes = clock_minutes(control_time)
    target_minutes = clock_minutes(target_time)

    # Automatic correction is deliberately limited to a same-day window before
    # the requested reading time. A control time at/after the target disables
    # automatic rebooting instead of risking a late or overnight reboot.
    if control_minutes >= target_minutes:
        return False, None
    if not control_minutes <= now_minutes < target_minutes:
        return False, None

    if last_detected.tzinfo is not None and now.tzinfo is not None:
        observed = last_detected.astimezone(now.tzinfo)
    else:
        observed = last_detected

    effective_reference = reference_time or target_time
    drift = circular_drift_minutes(observed, effective_reference)
    if abs(drift) <= tolerance_minutes:
        return False, None

    return (
        True,
        "dérive de relève gaz "
        f"{drift:+d} min (référence {clock_string(effective_reference)})",
    )
