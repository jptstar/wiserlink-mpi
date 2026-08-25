"""Polling coordinator for WiserLink MPI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WiserLinkClient, WiserLinkError
from .const import (
    CONF_GAS_CONTROL_TIME,
    CONF_GAS_DRIFT_CONTROL,
    CONF_GAS_TARGET_TIME,
    CONF_GAS_TOLERANCE_MINUTES,
    DEFAULT_GAS_CONTROL_TIME,
    DEFAULT_GAS_DRIFT_CONTROL,
    DEFAULT_GAS_TARGET_TIME,
    DEFAULT_GAS_TOLERANCE_MINUTES,
    DOMAIN,
)
from .gas_monitor import (
    circular_drift_minutes,
    clock_minutes,
    clock_string,
    next_estimated_reading,
    should_correct_drift,
)
from .meter import is_gas_meter
from .validation import snapshot_anomalies

_LOGGER = logging.getLogger(__name__)
_CONFIRM_DELAY_SECONDS = 1.0
_REBOOT_GRACE_SECONDS = 90.0
_STORAGE_VERSION = 1
_MIN_POST_REBOOT_SETTLE_MINUTES = 10


class WiserLinkCoordinator(DataUpdateCoordinator[dict]):
    """Fetch the complete bus snapshot once for all entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: WiserLinkClient,
        interval: int,
        failure_threshold: int,
        entry_id: str,
        settings: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0
        self._last_usage_monotonic: float | None = None
        self._reboot_grace_until = 0.0

        self._gas_drift_control = bool(
            settings.get(CONF_GAS_DRIFT_CONTROL, DEFAULT_GAS_DRIFT_CONTROL)
        )
        self._gas_target_time = str(
            settings.get(CONF_GAS_TARGET_TIME, DEFAULT_GAS_TARGET_TIME)
        )
        self._gas_tolerance_minutes = int(
            settings.get(
                CONF_GAS_TOLERANCE_MINUTES, DEFAULT_GAS_TOLERANCE_MINUTES
            )
        )
        self._gas_control_time = str(
            settings.get(CONF_GAS_CONTROL_TIME, DEFAULT_GAS_CONTROL_TIME)
        )

        self._gas_store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, f"{DOMAIN}.{entry_id}.gas_monitor"
        )
        self._gas_last_raw_value: Decimal | None = None
        self._gas_last_detected_at: datetime | None = None
        self._gas_last_reboot_at: datetime | None = None
        self._gas_last_reboot_reason: str | None = None
        self._gas_last_auto_reboot_date: str | None = None

        # Once an automatic reboot successfully moves the MPE close to the target,
        # the actual post-reboot publication time becomes the learned cycle
        # reference. Example: a 23:35 reboot leads to a stable 23:47 publication;
        # future drift is measured from 23:47 instead of rebooting every day to
        # force an exact 23:45.
        self._gas_cycle_reference_time: str | None = None
        self._gas_reference_target_time: str | None = None
        self._gas_awaiting_post_reboot_reading = False
        self._gas_pre_reboot_target_drift: int | None = None
        self._gas_auto_reboot_suspended = False
        self._gas_auto_reboot_suspend_reason: str | None = None

    async def async_initialize(self) -> None:
        """Load persistent gas drift history before first polling."""
        saved = await self._gas_store.async_load() or {}
        self._gas_last_raw_value = self._decimal(saved.get("last_raw_value"))
        self._gas_last_detected_at = self._parse_datetime(saved.get("last_detected_at"))
        self._gas_last_reboot_at = self._parse_datetime(saved.get("last_reboot_at"))
        self._gas_last_reboot_reason = saved.get("last_reboot_reason")
        self._gas_last_auto_reboot_date = saved.get("last_auto_reboot_date")

        saved_target = saved.get("reference_target_time")
        target_unchanged = False
        if saved_target:
            try:
                target_unchanged = (
                    clock_minutes(saved_target) == clock_minutes(self._gas_target_time)
                )
            except (TypeError, ValueError):
                target_unchanged = False

        if target_unchanged:
            reference = saved.get("cycle_reference_time")
            self._gas_cycle_reference_time = str(reference) if reference else None
            self._gas_reference_target_time = str(saved_target)
            self._gas_awaiting_post_reboot_reading = bool(
                saved.get("awaiting_post_reboot_reading", False)
            )
            pre_drift = saved.get("pre_reboot_target_drift")
            self._gas_pre_reboot_target_drift = (
                int(pre_drift) if pre_drift is not None else None
            )
            self._gas_auto_reboot_suspended = bool(
                saved.get("auto_reboot_suspended", False)
            )
            self._gas_auto_reboot_suspend_reason = saved.get(
                "auto_reboot_suspend_reason"
            )
        else:
            # A changed target is an explicit request for a new alignment cycle.
            self._gas_cycle_reference_time = None
            self._gas_reference_target_time = self._gas_target_time
            self._gas_awaiting_post_reboot_reading = False
            self._gas_pre_reboot_target_drift = None
            self._gas_auto_reboot_suspended = False
            self._gas_auto_reboot_suspend_reason = None

        if not self._gas_drift_control:
            # Disabling the feature is also a clean way to clear a previous
            # automatic-reboot suspension before enabling it again later.
            self._gas_awaiting_post_reboot_reading = False
            self._gas_pre_reboot_target_drift = None
            self._gas_auto_reboot_suspended = False
            self._gas_auto_reboot_suspend_reason = None

        await self._async_save_gas_state()

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return numeric if numeric.is_finite() else None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        return dt_util.parse_datetime(str(value))

    async def _async_save_gas_state(self) -> None:
        await self._gas_store.async_save(
            {
                "last_raw_value": (
                    str(self._gas_last_raw_value)
                    if self._gas_last_raw_value is not None
                    else None
                ),
                "last_detected_at": (
                    self._gas_last_detected_at.isoformat()
                    if self._gas_last_detected_at
                    else None
                ),
                "last_reboot_at": (
                    self._gas_last_reboot_at.isoformat()
                    if self._gas_last_reboot_at
                    else None
                ),
                "last_reboot_reason": self._gas_last_reboot_reason,
                "last_auto_reboot_date": self._gas_last_auto_reboot_date,
                "cycle_reference_time": self._gas_cycle_reference_time,
                "reference_target_time": self._gas_target_time,
                "awaiting_post_reboot_reading": self._gas_awaiting_post_reboot_reading,
                "pre_reboot_target_drift": self._gas_pre_reboot_target_drift,
                "auto_reboot_suspended": self._gas_auto_reboot_suspended,
                "auto_reboot_suspend_reason": self._gas_auto_reboot_suspend_reason,
            }
        )

    @staticmethod
    def _meters(data: dict) -> list:
        meters = data.get("UsageMeterList", [])
        return meters if isinstance(meters, list) else []

    @property
    def _gas_effective_reference_time(self) -> str:
        return self._gas_cycle_reference_time or self._gas_target_time

    async def _async_evaluate_post_reboot_reading(
        self, detected_at: datetime
    ) -> None:
        """Learn the corrected cycle or suspend useless repeated reboots."""
        if not self._gas_awaiting_post_reboot_reading:
            return
        if self._gas_last_reboot_at is None or detected_at <= self._gas_last_reboot_at:
            return

        self._gas_awaiting_post_reboot_reading = False
        target_drift = abs(
            circular_drift_minutes(detected_at, self._gas_target_time)
        )
        settle_limit = max(
            self._gas_tolerance_minutes, _MIN_POST_REBOOT_SETTLE_MINUTES
        )

        if target_drift <= settle_limit:
            self._gas_cycle_reference_time = clock_string(detected_at)
            self._gas_reference_target_time = self._gas_target_time
            self._gas_auto_reboot_suspended = False
            self._gas_auto_reboot_suspend_reason = None
            _LOGGER.info(
                "Recalage gaz validé: nouvelle référence de cycle %s "
                "(écart cible %d min)",
                self._gas_cycle_reference_time,
                target_drift,
            )
        else:
            previous = self._gas_pre_reboot_target_drift
            previous_text = f"{previous} min" if previous is not None else "inconnue"
            self._gas_auto_reboot_suspended = True
            self._gas_auto_reboot_suspend_reason = (
                "reboot correctif sans recalage suffisant "
                f"(dérive avant {previous_text}, après {target_drift} min)"
            )
            _LOGGER.warning(
                "Reboots automatiques gaz suspendus: %s",
                self._gas_auto_reboot_suspend_reason,
            )

        self._gas_pre_reboot_target_drift = None

    async def _async_observe_gas_reading(self, data: dict) -> None:
        """Remember when a new gas index is observed, without altering the value."""
        gas_meter = next(
            (
                meter
                for meter in self._meters(data)
                if isinstance(meter, dict) and is_gas_meter(meter)
            ),
            None,
        )
        if gas_meter is None:
            return

        raw = self._decimal(gas_meter.get("EnergyConsumed"))
        if raw is None:
            return

        previous = self._gas_last_raw_value
        self._gas_last_raw_value = raw

        # The first value establishes a baseline only. A later positive change,
        # including 0 -> a real value after a reboot, is a detected publication.
        detected = previous is not None and raw > 0 and raw != previous
        if detected:
            detected_at = dt_util.now()
            self._gas_last_detected_at = detected_at
            await self._async_evaluate_post_reboot_reading(detected_at)
            _LOGGER.info(
                "Nouvelle relève gaz détectée à %s, index=%s m³",
                detected_at.isoformat(timespec="seconds"),
                raw,
            )

        if previous != raw or detected:
            await self._async_save_gas_state()

    async def _async_read_usage_once(self) -> tuple[dict, float]:
        data = await self.client.async_get_usage_meters()
        await self._async_observe_gas_reading(data)
        return data, time.monotonic()

    async def _async_read_usage_with_retry(self) -> tuple[dict, float]:
        """Retry one rejected/failed UsageMeter read before giving up."""
        try:
            return await self._async_read_usage_once()
        except WiserLinkError as first_error:
            _LOGGER.warning(
                "Lecture UsageMeter invalide, nouvelle lecture dans %.1f s: %s",
                _CONFIRM_DELAY_SECONDS,
                first_error,
            )
            await asyncio.sleep(_CONFIRM_DELAY_SECONDS)
            try:
                return await self._async_read_usage_once()
            except WiserLinkError as second_error:
                raise WiserLinkError(
                    "Deux lectures UsageMeter successives ont échoué: "
                    f"{first_error}; {second_error}"
                ) from second_error

    async def _async_initial_usage(self) -> tuple[dict, float]:
        """Require two consecutive coherent samples before first publication."""
        first, first_at = await self._async_read_usage_with_retry()
        await asyncio.sleep(_CONFIRM_DELAY_SECONDS)
        second, second_at = await self._async_read_usage_with_retry()

        first_second = snapshot_anomalies(
            self._meters(first), self._meters(second), second_at - first_at
        )
        if not first_second:
            return second, second_at

        _LOGGER.warning(
            "Lectures UsageMeter incohérentes au démarrage (%s), "
            "troisième lecture de confirmation",
            ", ".join(first_second),
        )
        await asyncio.sleep(_CONFIRM_DELAY_SECONDS)
        third, third_at = await self._async_read_usage_with_retry()

        second_third = snapshot_anomalies(
            self._meters(second), self._meters(third), third_at - second_at
        )
        if not second_third:
            return third, third_at

        raise WiserLinkError(
            "Aucune paire de lectures UsageMeter consécutives cohérentes "
            "au démarrage: "
            f"1→2 [{', '.join(first_second)}], "
            f"2→3 [{', '.join(second_third)}]"
        )

    async def _async_confirmed_usage(self) -> tuple[dict, float]:
        """Read UsageMeter and confirm any suspicious continuity change."""
        if self.data is None or self._last_usage_monotonic is None:
            return await self._async_initial_usage()

        candidate, candidate_at = await self._async_read_usage_with_retry()
        previous_meters = self._meters(self.data)
        candidate_anomalies = snapshot_anomalies(
            previous_meters,
            self._meters(candidate),
            candidate_at - self._last_usage_monotonic,
        )
        if not candidate_anomalies:
            return candidate, candidate_at

        _LOGGER.warning(
            "Lecture UsageMeter suspecte (%s), confirmation dans %.1f s",
            ", ".join(candidate_anomalies),
            _CONFIRM_DELAY_SECONDS,
        )
        await asyncio.sleep(_CONFIRM_DELAY_SECONDS)
        confirmation, confirmation_at = await self._async_read_usage_with_retry()

        confirmation_vs_previous = snapshot_anomalies(
            previous_meters,
            self._meters(confirmation),
            confirmation_at - self._last_usage_monotonic,
        )
        if not confirmation_vs_previous:
            _LOGGER.info("La lecture suspecte était transitoire; valeur saine conservée")
            return confirmation, confirmation_at

        confirmation_vs_candidate = snapshot_anomalies(
            self._meters(candidate),
            self._meters(confirmation),
            confirmation_at - candidate_at,
        )
        if not confirmation_vs_candidate:
            _LOGGER.warning(
                "Nouvelle base UsageMeter confirmée après deux lectures cohérentes: %s",
                ", ".join(confirmation_vs_previous),
            )
            return confirmation, confirmation_at

        raise WiserLinkError(
            "Lecture UsageMeter non confirmée: "
            f"candidate [{', '.join(candidate_anomalies)}], "
            f"confirmation [{', '.join(confirmation_vs_previous)}]"
        )

    async def async_reboot(self, reason: str = "manuel", automatic: bool = False) -> None:
        """Reboot the MIP through the confirmed local /rs session API."""
        if automatic:
            # Save the state before sending the command so a Home Assistant reload
            # immediately after the request cannot cause a second reboot.
            now = dt_util.now()
            self._gas_last_auto_reboot_date = now.date().isoformat()
            self._gas_awaiting_post_reboot_reading = True
            self._gas_pre_reboot_target_drift = (
                abs(
                    circular_drift_minutes(
                        self._gas_last_detected_at, self._gas_target_time
                    )
                )
                if self._gas_last_detected_at is not None
                else None
            )
            await self._async_save_gas_state()

        try:
            await self.client.async_reboot()
        except WiserLinkError:
            if automatic:
                self._gas_last_auto_reboot_date = None
                self._gas_awaiting_post_reboot_reading = False
                self._gas_pre_reboot_target_drift = None
                await self._async_save_gas_state()
            raise

        now = dt_util.now()
        self._gas_last_reboot_at = now
        self._gas_last_reboot_reason = reason
        self._reboot_grace_until = time.monotonic() + _REBOOT_GRACE_SECONDS
        await self._async_save_gas_state()
        _LOGGER.warning("Redémarrage Wiser MIP demandé: %s", reason)

    async def _async_maybe_correct_gas_drift(self) -> None:
        """Reboot once, early, only when a learned gas cycle is truly drifting."""
        if not self._gas_drift_control or self._gas_last_detected_at is None:
            return
        if self._gas_auto_reboot_suspended:
            return
        if self._gas_awaiting_post_reboot_reading:
            return

        now = dt_util.now()
        if self._gas_last_auto_reboot_date == now.date().isoformat():
            return

        # Never reboot repeatedly from the same old observation. After any reboot,
        # a new real MPE gas publication is required before another correction.
        if (
            self._gas_last_reboot_at is not None
            and self._gas_last_detected_at <= self._gas_last_reboot_at
        ):
            return

        should_reboot, reason = should_correct_drift(
            now=now,
            last_detected=self._gas_last_detected_at,
            target_time=self._gas_target_time,
            reference_time=self._gas_effective_reference_time,
            tolerance_minutes=self._gas_tolerance_minutes,
            control_time=self._gas_control_time,
        )
        if not should_reboot or reason is None:
            return

        try:
            await self.async_reboot(reason, automatic=True)
        except WiserLinkError as err:
            _LOGGER.error("Correction automatique de dérive gaz impossible: %s", err)

    async def _async_update_data(self) -> dict:
        try:
            data, usage_at = await self._async_confirmed_usage()
        except WiserLinkError as err:
            self.consecutive_failures += 1
            if self.data is not None and (
                self.consecutive_failures < self.failure_threshold
                or time.monotonic() < self._reboot_grace_until
            ):
                _LOGGER.warning(
                    "Lecture MPI échouée (%s/%s), dernières valeurs conservées: %s",
                    self.consecutive_failures,
                    self.failure_threshold,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        self._last_usage_monotonic = usage_at

        try:
            data["_sem_identification"] = await self.client.async_get_sem_identification()
        except WiserLinkError as err:
            _LOGGER.warning("Lecture des statuts EM5 impossible: %s", err)
            if self.data is not None and "_sem_identification" in self.data:
                data["_sem_identification"] = self.data["_sem_identification"]
        try:
            data["_mip_identification"] = await self.client.async_get_mip_identification()
        except WiserLinkError as err:
            _LOGGER.warning("Lecture de l’identification MIP impossible: %s", err)
            if self.data is not None and "_mip_identification" in self.data:
                data["_mip_identification"] = self.data["_mip_identification"]
        try:
            data["_mpr_instances"] = await self.client.async_get_mpr_instances()
        except WiserLinkError as err:
            _LOGGER.warning("Lecture des compteurs MPR impossible: %s", err)
            if self.data is not None and "_mpr_instances" in self.data:
                data["_mpr_instances"] = self.data["_mpr_instances"]
        try:
            data["_events"] = await self.client.async_get_events()
        except WiserLinkError as err:
            _LOGGER.warning("Lecture des événements impossible: %s", err)
            if self.data is not None and "_events" in self.data:
                data["_events"] = self.data["_events"]

        data["_gas_monitor"] = self.gas_monitor_data
        self.consecutive_failures = 0
        await self._async_maybe_correct_gas_drift()
        return data

    @property
    def gas_monitor_data(self) -> dict[str, Any]:
        """Return gas timing diagnostics for Home Assistant entities."""
        drift = self.gas_drift_minutes
        target_drift = (
            circular_drift_minutes(self._gas_last_detected_at, self._gas_target_time)
            if self._gas_last_detected_at is not None
            else None
        )
        correction_window_valid = (
            clock_minutes(self._gas_control_time)
            < clock_minutes(self._gas_target_time)
        )
        return {
            "raw_value": (
                float(self._gas_last_raw_value)
                if self._gas_last_raw_value is not None
                else None
            ),
            "last_detected_at": self._gas_last_detected_at,
            "next_estimated_at": next_estimated_reading(self._gas_last_detected_at),
            "drift_minutes": drift,
            "target_drift_minutes": target_drift,
            "drift_exceeded": (
                abs(drift) > self._gas_tolerance_minutes if drift is not None else None
            ),
            "target_time": self._gas_target_time,
            "cycle_reference_time": self._gas_cycle_reference_time,
            "effective_reference_time": self._gas_effective_reference_time,
            "tolerance_minutes": self._gas_tolerance_minutes,
            "control_time": self._gas_control_time,
            "correction_window_valid": correction_window_valid,
            "automatic_control": self._gas_drift_control,
            "waiting_for_new_reading_after_reboot": (
                self._gas_awaiting_post_reboot_reading
            ),
            "auto_reboot_suspended": self._gas_auto_reboot_suspended,
            "auto_reboot_suspend_reason": self._gas_auto_reboot_suspend_reason,
            "last_reboot_at": self._gas_last_reboot_at,
            "last_reboot_reason": self._gas_last_reboot_reason,
        }

    @property
    def gas_drift_minutes(self) -> int | None:
        if self._gas_last_detected_at is None:
            return None
        return circular_drift_minutes(
            self._gas_last_detected_at, self._gas_effective_reference_time
        )
