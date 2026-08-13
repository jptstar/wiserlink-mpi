"""WiserLink MPI integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WiserLinkClient, WiserLinkError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    DOMAIN,
    PLATFORMS,
    SERVICE_SEND_COMMAND,
)
from .coordinator import WiserLinkCoordinator

type WiserLinkConfigEntry = ConfigEntry[WiserLinkCoordinator]

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("method"): vol.In(["POST", "PUT", "PATCH"]),
        vol.Required("path"): cv.string,
        vol.Optional("payload", default={}): dict,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: WiserLinkConfigEntry) -> bool:
    """Set up one MPI."""
    settings = {**entry.data, **entry.options}
    client = WiserLinkClient(
        async_get_clientsession(hass),
        settings[CONF_HOST],
        settings[CONF_PORT],
        settings[CONF_USERNAME],
        settings[CONF_PASSWORD],
    )
    coordinator = WiserLinkCoordinator(
        hass,
        client,
        settings[CONF_SCAN_INTERVAL],
        settings.get(CONF_FAILURE_THRESHOLD, 3),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        async def async_send_command(call: ServiceCall) -> dict[str, Any]:
            target = hass.config_entries.async_get_entry(call.data["entry_id"])
            if target is None or target.domain != DOMAIN or target.runtime_data is None:
                raise ServiceValidationError("Configuration WiserLink MPI introuvable")
            try:
                response = await target.runtime_data.client.async_send_command(
                    call.data["method"], call.data["path"], call.data["payload"]
                )
            except WiserLinkError as err:
                raise HomeAssistantError(str(err)) from err
            await target.runtime_data.async_request_refresh()
            return {"response": response}

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            async_send_command,
            schema=SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: WiserLinkConfigEntry) -> None:
    """Apply edited IP, credentials, interval and load names."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WiserLinkConfigEntry) -> bool:
    """Unload one MPI."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        item.entry_id != entry.entry_id and item.domain == DOMAIN
        for item in hass.config_entries.async_entries(DOMAIN)
    ):
        hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
    return unloaded
