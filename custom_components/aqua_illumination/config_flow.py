"""UI configuration and validated IP changes for AI Wi-Fi lights."""

import asyncio
from ipaddress import ip_address

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .aquaipy import AquaIPy
from .aquaipy.error import ConnError, FirmwareError, MustBeParentError
from .const import (
    CONF_ENABLE_CONTROL,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

POLL_INTERVAL_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=10, max=60))


async def _async_validate(hass, user_input):
    """Validate the local API without changing any light setting."""
    host = str(ip_address(user_input["host"].strip()))
    api = AquaIPy(session=async_get_clientsession(hass))
    async with asyncio.timeout(30):
        await api.async_connect(host)
        await api.async_get_state()
    return host, api.mac_addr


def _error_key(error):
    """Map expected validation failures to translated form messages."""
    if isinstance(error, FirmwareError):
        return "unsupported_firmware"
    if isinstance(error, MustBeParentError):
        return "not_parent"
    if isinstance(error, ValueError):
        return "invalid_host"
    return "cannot_connect"


class AquaIlluminationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a parent light via its local IP address."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                host, mac = await _async_validate(self.hass, user_input)
            except (
                ConnError,
                FirmwareError,
                MustBeParentError,
                TimeoutError,
                ValueError,
            ) as err:
                errors["base"] = _error_key(err)
            else:
                # AbortFlow must not be caught as an unexpected connection error.
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                name = user_input["name"].strip() or "Aqua Illumination"
                return self.async_create_entry(
                    title=name, data={"host": host, "name": name}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("host"): str,
                    vol.Required("name", default="Aqua Illumination"): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AquaIlluminationOptionsFlow()


class AquaIlluminationOptionsFlow(config_entries.OptionsFlowWithReload):
    """Validate an IP change and reload automatically after saving options."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                poll_interval = POLL_INTERVAL_SCHEMA(
                    user_input.get(
                        CONF_POLL_INTERVAL,
                        self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    )
                )
                host, mac = await _async_validate(self.hass, user_input)
            except vol.Invalid:
                errors["base"] = "invalid_poll_interval"
            except (
                ConnError,
                FirmwareError,
                MustBeParentError,
                TimeoutError,
                ValueError,
            ) as err:
                errors["base"] = _error_key(err)
            else:
                if self.config_entry.unique_id and mac != self.config_entry.unique_id:
                    errors["base"] = "wrong_device"
                else:
                    return self.async_create_entry(
                        title="",
                        data={
                            "host": host,
                            CONF_ENABLE_CONTROL: user_input.get(
                                CONF_ENABLE_CONTROL,
                                self.config_entry.options.get(
                                    CONF_ENABLE_CONTROL, False
                                ),
                            ),
                            CONF_POLL_INTERVAL: poll_interval,
                        },
                    )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "host",
                        default=self.config_entry.options.get(
                            "host", self.config_entry.data["host"]
                        ),
                    ): str,
                    vol.Required(
                        CONF_ENABLE_CONTROL,
                        default=self.config_entry.options.get(
                            CONF_ENABLE_CONTROL, False
                        ),
                    ): bool,
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): POLL_INTERVAL_SCHEMA,
                }
            ),
            errors=errors,
        )
