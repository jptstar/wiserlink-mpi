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
        self.consecutive_failures = 0
        return data
