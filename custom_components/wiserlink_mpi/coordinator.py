"""Polling coordinator for WiserLink MPI."""

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WiserLinkClient, WiserLinkError
from .const import DOMAIN
from .validation import snapshot_anomalies

_LOGGER = logging.getLogger(__name__)
_CONFIRM_DELAY_SECONDS = 1.0


class WiserLinkCoordinator(DataUpdateCoordinator[dict]):
    """Fetch the complete bus snapshot once for all entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: WiserLinkClient,
        interval: int,
        failure_threshold: int,
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

    @staticmethod
    def _meters(data: dict) -> list:
        meters = data.get("UsageMeterList", [])
        return meters if isinstance(meters, list) else []

    async def _async_read_usage_once(self) -> tuple[dict, float]:
        data = await self.client.async_get_usage_meters()
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

    async def _async_update_data(self) -> dict:
        try:
            data, usage_at = await self._async_confirmed_usage()
        except WiserLinkError as err:
            self.consecutive_failures += 1
            if self.data is not None and self.consecutive_failures < self.failure_threshold:
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
        self.consecutive_failures = 0
        return data
