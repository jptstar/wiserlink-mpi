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
    CONF_FAILURE_THRESHOLD,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_CONFIGURE_MPR,
    SERVICE_DELETE_MPR,
    SERVICE_REBOOT_MIP,
    SERVICE_SEND_COMMAND,
)
from .coordinator import WiserLinkCoordinator
from .migration import migrate_meter_identities

type WiserLinkConfigEntry = ConfigEntry[WiserLinkCoordinator]

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("method"): vol.In(["POST", "PUT", "PATCH"]),
        vol.Required("path"): cv.string,
        vol.Optional("payload", default={}): dict,
    }
)

CONFIGURE_MPR_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("meter_id"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
        vol.Required("meter_type"): vol.In(
            ["Cold Water Meter", "Hot Water Meter", "Gas Meter", "Calorimeter"]
        ),
        vol.Required("rt2012_usage"): vol.In(
            ["No usage", "Heating", "Hot water", "Cooling", "Sockets", "Others"]
        ),
        vol.Required("pulse_weight"): vol.All(
            vol.Coerce(float), vol.Range(min=0.001)
        ),
        vol.Required("pulse_weight_unit"): vol.In(
            ["Litre", "m3", "dm3", "kWh", "Wh"]
        ),
        vol.Required("radio_address"): vol.Match(r"^[0-9]{15}$"),
    }
)

DELETE_MPR_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("meter_id"): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
    }
)

REBOOT_MIP_SCHEMA = vol.Schema({vol.Required("entry_id"): cv.string})


def _get_target_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    """Resolve an explicitly selected WiserLink MPI configuration."""
    target = hass.config_entries.async_get_entry(entry_id)
    if target is None or target.domain != DOMAIN or target.runtime_data is None:
        raise ServiceValidationError("Configuration WiserLink MPI introuvable")
    return target


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
        entry.entry_id,
        settings,
    )
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Repair old index-based entities before sensor setup. This lets the platform
    # bind the current semantic entity directly to the user's historical entity_id
    # and statistics instead of creating a duplicate (for example Teleinfo Conso
    # Total vs Compteur électrique).
    meters = [
        meter
        for meter in coordinator.data.get("UsageMeterList", [])
        if isinstance(meter, dict)
    ]
    migrate_meter_identities(hass, entry, meters)

    # Register the options listener only after platform setup so the one-time
    # migration above cannot trigger a reload while the entry is initializing.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):

        async def async_send_command(call: ServiceCall) -> dict[str, Any]:
            target = _get_target_entry(hass, call.data["entry_id"])
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

        async def async_configure_mpr(call: ServiceCall) -> dict[str, Any]:
            target = _get_target_entry(hass, call.data["entry_id"])
            try:
                result = await target.runtime_data.client.async_configure_mpr(
                    call.data["meter_id"],
                    call.data["meter_type"],
                    call.data["rt2012_usage"],
                    call.data["pulse_weight"],
                    call.data["pulse_weight_unit"],
                    call.data["radio_address"],
                )
            except WiserLinkError as err:
                raise HomeAssistantError(str(err)) from err
            await target.runtime_data.async_request_refresh()
            return {
                "meter_id": result["Id"],
                "meter_type": result["Type"],
                "status": "unchanged" if result.get("_unchanged") else "configured",
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_CONFIGURE_MPR,
            async_configure_mpr,
            schema=CONFIGURE_MPR_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        async def async_delete_mpr(call: ServiceCall) -> dict[str, Any]:
            target = _get_target_entry(hass, call.data["entry_id"])
            try:
                await target.runtime_data.client.async_delete_mpr(
                    call.data["meter_id"]
                )
            except WiserLinkError as err:
                raise HomeAssistantError(str(err)) from err
            await target.runtime_data.async_request_refresh()
            return {"meter_id": call.data["meter_id"], "status": "deleted"}

        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_MPR,
            async_delete_mpr,
            schema=DELETE_MPR_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        async def async_reboot_mip(call: ServiceCall) -> dict[str, Any]:
            target = _get_target_entry(hass, call.data["entry_id"])
            try:
                await target.runtime_data.async_reboot("service Home Assistant")
            except WiserLinkError as err:
                raise HomeAssistantError(str(err)) from err
            return {"status": "reboot_requested"}

        hass.services.async_register(
            DOMAIN,
            SERVICE_REBOOT_MIP,
            async_reboot_mip,
            schema=REBOOT_MIP_SCHEMA,
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
        hass.services.async_remove(DOMAIN, SERVICE_CONFIGURE_MPR)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_MPR)
        hass.services.async_remove(DOMAIN, SERVICE_REBOOT_MIP)
    return unloaded
