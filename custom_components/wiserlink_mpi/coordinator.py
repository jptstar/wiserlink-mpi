"""Polling coordinator for WiserLink MPI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
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
    decimal_value,
    next_estimated_reading,
    protected_cumulative_value,
    should_correct_drift,
)
from .meter import is_gas_meter
from .validation import snapshot_anomalies

_LOGGER = logging.getLogger(__name__)
_CONFIRM_DELAY_SECONDS = 1.0
_REBOOT_GRACE_SECONDS = 90.0
_STORAGE_VERSION = 1


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
        self._gas_last_valid_value: Decimal | None = None
        self._gas_last_raw_value: Decimal | None = None
        self._gas_last_detected_at: datetime | None = None
        self._gas_last_reboot_at: datetime | None = None
        self._gas_last_reboot_reason: str | None = None
        self._gas_last_auto_reboot_date: str | None = None
        self._gas_cache_protected = False
        self._gas_raw_value: Decimal | None = None

    async def async_initialize(self) -> None:
        """Load persistent gas protection and drift history before first polling."""
        saved = await self._gas_store.async_load() or {}
        self._gas_last_valid_value = decimal_value(saved.get("last_valid_value"))
        self._gas_last_raw_value = decimal_value(saved.get("last_raw_value"))
        self._gas_last_detected_at = self._parse_datetime(saved.get("last_detected_at"))
        self._gas_last_reboot_at = self._parse_datetime(saved.get("last_reboot_at"))
        self._gas_last_reboot_reason = saved.get("last_reboot_reason")
        self._gas_last_auto_reboot_date = saved.get("last_auto_reboot_date")

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        parsed = dt_util.parse_datetime(str(value))
        return parsed

    async def _async_save_gas_state(self) -> None:
        await self._gas_store.async_save(
            {
                "last_valid_value": (
                    str(self._gas_last_valid_value)
                    if self._gas_last_valid_value is not None
                    else None
                ),
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
            }
        )

    @staticmethod
    def _meters(data: dict) -> list:
        meters = data.get("UsageMeterList", [])
        return meters if isinstance(meters, list) else []

    async def _async_apply_gas_guard(self, data: dict) -> None:
        """Keep a known cumulative gas index across MIP cache resets."""
        meters = self._meters(data)
        gas_meter = next(
            (meter for meter in meters if isinstance(meter, dict) and is_gas_meter(meter)),
            None,
        )
        if gas_meter is None:
            return

        raw = decimal_value(gas_meter.get("EnergyConsumed"))
        previous_raw = self._gas_last_raw_value
        previous_valid = self._gas_last_valid_value
        now = dt_util.utcnow()
        changed = raw != previous_raw
        detected = False

        if raw is not None and raw >= 0:
            if previous_valid is None:
                if raw > 0:
                    self._gas_last_valid_value = raw
                    detected = previous_raw is not None and previous_raw <= 0
            elif raw > previous_valid:
                self._gas_last_valid_value = raw
                detected = True
            elif (
                raw >= previous_valid
                and previous_raw is not None
                and previous_raw < previous_valid
            ):
                # A reboot commonly clears the MIP gas cache to 0. Seeing the
                # protected index again is a detectable fresh MPR/MPE recovery,
                # even when there was no gas consumption in between.
                detected = True

        protected, is_protected = protected_cumulative_value(
            raw, self._gas_last_valid_value
        )
        self._gas_cache_protected = is_protected
        self._gas_raw_value = raw
        self._gas_last_raw_value = raw

        if is_protected and protected is not None:
            gas_meter["EnergyConsumed"] = float(protected)
            _LOGGER.warning(
                "Index gaz brut %s m³ inférieur au dernier index valide %s m³; "
                "dernière valeur valide conservée",
                raw,
                protected,
            )

        if detected:
            self._gas_last_detected_at = now
            _LOGGER.info(
                "Nouvelle relève gaz détectée à %s, index=%s m³",
                dt_util.as_local(now).isoformat(timespec="seconds"),
                self._gas_last_valid_value,
            )
            changed = True

        if self._gas_last_valid_value != previous_valid:
            changed = True
        if changed:
            await self._async_save_gas_state()

    async def _async_read_usage_once(self) -> tuple[dict, float]:
        data = await self.client.async_get_usage_meters()
        await self._async_apply_gas_guard(data)
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
            self._meters(first),
            self._meters(second),
            second_at - first_at,
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
            self._meters(second),
            self._meters(third),
            third_at - second_at,
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
        """Reboot the MIP and preserve gas data during the expected outage."""
        await self.client.async_reboot()
        now = dt_util.utcnow()
        self._gas_last_reboot_at = now
        self._gas_last_reboot_reason = reason
        self._reboot_grace_until = time.monotonic() + _REBOOT_GRACE_SECONDS
        if automatic:
            self._gas_last_auto_reboot_date = dt_util.as_local(now).date().isoformat()
        await self._async_save_gas_state()
        _LOGGER.warning("Redémarrage Wiser MIP demandé: %s", reason)

    async def _async_maybe_correct_gas_drift(self) -> None:
        if not self._gas_drift_control or self._gas_last_detected_at is None:
            return

        now = dt_util.now()
        if self._gas_last_auto_reboot_date == now.date().isoformat():
            return

        last_local = dt_util.as_local(self._gas_last_detected_at)
        should_reboot, reason = should_correct_drift(
            now=now,
            last_detected=last_local,
            target_time=self._gas_target_time,
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
            data["_sem_identification"] = (
                await self.client.async_get_sem_identification()
            )
        except WiserLinkError as err:
            _LOGGER.warning("Lecture des statuts EM5 impossible: %s", err)
            if self.data is not None and "_sem_identification" in self.data:
                data["_sem_identification"] = self.data["_sem_identification"]
        try:
            data["_mip_identification"] = (
                await self.client.async_get_mip_identification()
            )
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
        """Return diagnostics used by the gas drift entity."""
        drift = self.gas_drift_minutes
        return {
            "last_valid_value": (
                float(self._gas_last_valid_value)
                if self._gas_last_valid_value is not None
                else None
            ),
            "raw_value": (
                float(self._gas_raw_value) if self._gas_raw_value is not None else None
            ),
            "cache_protected": self._gas_cache_protected,
            "last_detected_at": self._gas_last_detected_at,
            "next_estimated_at": next_estimated_reading(self._gas_last_detected_at),
            "drift_minutes": drift,
            "drift_exceeded": (
                abs(drift) > self._gas_tolerance_minutes if drift is not None else None
            ),
            "target_time": self._gas_target_time,
            "tolerance_minutes": self._gas_tolerance_minutes,
            "control_time": self._gas_control_time,
            "automatic_control": self._gas_drift_control,
            "last_reboot_at": self._gas_last_reboot_at,
            "last_reboot_reason": self._gas_last_reboot_reason,
        }

    @property
    def gas_drift_minutes(self) -> int | None:
        if self._gas_last_detected_at is None:
            return None
        return circular_drift_minutes(
            dt_util.as_local(self._gas_last_detected_at), self._gas_target_time
        )
