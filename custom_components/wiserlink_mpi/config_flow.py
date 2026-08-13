"""UI configuration flow for WiserLink MPI."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WiserLinkAuthError, WiserLinkClient, WiserLinkError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_ENABLE_GAS,
    CONF_ENABLE_WATER,
    CONF_LOAD_NAME_PREFIX,
    DEFAULT_METER_NAMES,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)


class WiserLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a WiserLink MPI from the Home Assistant UI."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> WiserLinkOptionsFlow:
        """Return the editable connection and load-name options flow."""
        return WiserLinkOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_configured()
            client = WiserLinkClient(
                async_get_clientsession(self.hass),
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_get_usage_meters()
            except WiserLinkAuthError:
                errors["base"] = "invalid_auth"
            except WiserLinkError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"WiserLink MPI ({user_input[CONF_HOST]})", data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=2, max=300)
                ),
                vol.Required(
                    CONF_FAILURE_THRESHOLD, default=DEFAULT_FAILURE_THRESHOLD
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
                vol.Required(CONF_ENABLE_GAS, default=False): bool,
                vol.Required(CONF_ENABLE_WATER, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class WiserLinkOptionsFlow(OptionsFlow):
    """Edit network settings and friendly names for detected loads."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save all editable settings."""
        current = {**self._entry.data, **self._entry.options}
        coordinator = self._entry.runtime_data
        meters = coordinator.data.get("UsageMeterList", []) if coordinator else []
        errors: dict[str, str] = {}

        if user_input is not None:
            client = WiserLinkClient(
                async_get_clientsession(self.hass),
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_get_usage_meters()
            except WiserLinkAuthError:
                errors["base"] = "invalid_auth"
            except WiserLinkError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="", data=user_input)

        fields: dict = {
            vol.Required(CONF_HOST, default=current[CONF_HOST]): str,
            vol.Required(CONF_PORT, default=current[CONF_PORT]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USERNAME, default=current[CONF_USERNAME]): str,
            vol.Required(CONF_PASSWORD, default=current[CONF_PASSWORD]): str,
            vol.Required(
                CONF_SCAN_INTERVAL, default=current[CONF_SCAN_INTERVAL]
            ): vol.All(vol.Coerce(int), vol.Range(min=2, max=300)),
            vol.Required(
                CONF_FAILURE_THRESHOLD,
                default=current.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
            vol.Required(CONF_ENABLE_GAS, default=current.get(CONF_ENABLE_GAS, False)): bool,
            vol.Required(
                CONF_ENABLE_WATER, default=current.get(CONF_ENABLE_WATER, False)
            ): bool,
        }
        for index, meter in enumerate(meters):
            if not isinstance(meter, dict):
                continue
            if index == 10 and not current.get(CONF_ENABLE_GAS, False):
                continue
            if index == 11 and not current.get(CONF_ENABLE_WATER, False):
                continue
            key = f"{CONF_LOAD_NAME_PREFIX}{index}"
            detected = next(
                (
                    str(meter[name_key])
                    for name_key in ("Name", "Label", "name", "label")
                    if meter.get(name_key)
                ),
                DEFAULT_METER_NAMES.get(index, f"Load {index + 1}"),
            )
            fields[vol.Required(key, default=current.get(key, detected))] = str

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(fields), errors=errors
        )
