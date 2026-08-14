"""Polling coordinator for WiserLink MPI."""

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WiserLinkClient, WiserLinkError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.async_get_usage_meters()
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
