"""Coordinate reads and serialize commands to an AI parent light."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .aquaipy.aquaipy import Response
from .aquaipy.error import ConnError, FirmwareError, MustBeParentError
from .const import CONF_ENABLE_CONTROL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AquaIlluminationCoordinator(DataUpdateCoordinator):
    """Keep all entities on one polling cycle and one device lock."""

    def __init__(self, hass, entry, api, host):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=10),
        )
        self.api = api
        self.host = host
        self.control_enabled = entry.options.get(CONF_ENABLE_CONTROL, False)
        self.platforms = []
        self._connected = False
        self._device_lock = asyncio.Lock()

    async def _async_read(self):
        """Read one complete snapshot while holding the device lock."""
        if not self._connected:
            await self.api.async_connect(self.host)
            # Do not control a different light after an IP address is reassigned.
            if (
                self.config_entry.unique_id
                and self.api.mac_addr != self.config_entry.unique_id
            ):
                raise ConnError(
                    "The IP address belongs to a different light", self.host
                )
            self._connected = True
        return await self.api.async_get_state()

    async def _async_update_data(self):
        _LOGGER.debug("Refreshing light state (read only)")
        try:
            async with self._device_lock, asyncio.timeout(30):
                return await self._async_read()
        except (ConnError, FirmwareError, MustBeParentError, TimeoutError) as err:
            self._connected = False
            raise UpdateFailed(str(err)) from err

    async def async_set_channel(self, channel, brightness):
        """Change one channel without losing concurrent updates to other channels."""
        await self._async_command(channel=channel, brightness=brightness)

    async def async_set_schedule(self, enabled):
        """Change the schedule only on an explicit switch command."""
        await self._async_command(schedule=enabled)

    async def _async_command(self, *, channel=None, brightness=None, schedule=None):
        if not self.control_enabled:
            raise HomeAssistantError(
                "Manual control is disabled; the integration is read only"
            )
        try:
            async with self._device_lock, asyncio.timeout(30):
                state = await self._async_read()
                if channel is not None:
                    colors = dict(state["colors"])
                    colors[channel] = brightness
                    result = await self.api.async_set_colors_brightness(colors)
                else:
                    result = await self.api.async_set_schedule_state(schedule)
                if result != Response.Success:
                    raise HomeAssistantError(
                        f"Light rejected the command: {result.name}"
                    )
                # Refresh within the same lock; publish only confirmed device state.
                self.async_set_updated_data(await self._async_read())
        except (ConnError, FirmwareError, MustBeParentError, TimeoutError) as err:
            self._connected = False
            self.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(
                f"Unable to communicate with the light: {err}"
            ) from err
