"""Coordinate bounded reads and serialize commands to an AI parent light."""

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .aquaipy.aquaipy import Response
from .aquaipy.error import (
    ConnError,
    FirmwareError,
    MustBeParentError,
    TransientConnectionError,
)
from .const import (
    CACHE_GRACE_SECONDS,
    CONF_ENABLE_CONTROL,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    READ_RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)
_READ_ERRORS = (ConnError, FirmwareError, MustBeParentError, TimeoutError)


class AquaIlluminationCoordinator(DataUpdateCoordinator):
    """Keep all entities on one polling cycle and one device lock."""

    def __init__(self, hass, entry, api, host):
        self.poll_interval = entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=self.poll_interval),
        )
        self.api = api
        self.host = host
        self.control_enabled = entry.options.get(CONF_ENABLE_CONTROL, False)
        self.platforms = []
        self.last_successful_update = None
        self.consecutive_failures = 0
        self.failed_updates = 0
        self.read_retries = 0
        self.last_read_error = None
        self._connected = False
        self._device_lock = asyncio.Lock()
        self._last_success_monotonic = None
        self._cache_allowed = False
        self._using_cached_data = False
        self._cancel_expiry = None
        self._active_poll = None

    @property
    def data_stale(self):
        """Whether the displayed snapshot has survived a failed read."""
        return self._using_cached_data

    @property
    def connection_status(self):
        if not self.last_update_success:
            return "Disconnected"
        return "Reconnecting" if self.data_stale else "Connected"

    @property
    def connection_diagnostics(self):
        """Connection history without addresses or raw exception messages."""
        return {
            "data_stale": self.data_stale,
            "consecutive_failed_updates": self.consecutive_failures,
            "failed_updates_since_start": self.failed_updates,
            "read_retries_since_start": self.read_retries,
            "poll_interval_seconds": self.poll_interval,
            "current_interval_seconds": self.update_interval.total_seconds(),
            "cache_grace_seconds": CACHE_GRACE_SECONDS,
            "last_read_error": self.last_read_error,
        }

    def _cancel_cache_expiry(self):
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    def _record_success(self, state):
        self._cancel_cache_expiry()
        self.last_successful_update = dt_util.utcnow()
        self._last_success_monotonic = monotonic()
        self._cache_allowed = True
        self._using_cached_data = False
        self.consecutive_failures = 0
        self.update_interval = timedelta(seconds=self.poll_interval)
        return state

    def _remember_read_error(self, err):
        self.last_read_error = {
            "type": type(err).__name__,
            "cause": type(err.__cause__).__name__ if err.__cause__ else None,
            "endpoint": getattr(err, "endpoint", None),
            "time": dt_util.utcnow().isoformat(),
        }

    def _invalidate_cache(self):
        """Never reuse a snapshot after an uncertain write or invalid identity."""
        self._connected = False
        self._cache_allowed = False
        self._using_cached_data = self.data is not None
        self._cancel_cache_expiry()

    @callback
    def _async_expire_cache(self, _now):
        """Expire independently of backoff or an in-flight network request."""
        self._cancel_expiry = None
        if self._cache_allowed and self.data_stale:
            self._cache_allowed = False
            self.async_set_update_error(UpdateFailed("Last light reading is too old"))

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

    async def _async_poll(self):
        """Retry one transient read; persistent outages use single attempts."""
        for attempt in range(2):
            try:
                return await self._async_read()
            except (TransientConnectionError, TimeoutError) as err:
                self._connected = False
                self._remember_read_error(err)
                if attempt or self.consecutive_failures:
                    raise
                self.read_retries += 1
                await asyncio.sleep(READ_RETRY_DELAY)

    async def _async_update_data(self):
        _LOGGER.debug("Refreshing light state (read only)")
        self._active_poll = asyncio.current_task()
        try:
            async with self._device_lock, asyncio.timeout(30):
                return self._record_success(await self._async_poll())
        except _READ_ERRORS as err:
            self._connected = False
            self.consecutive_failures += 1
            self.failed_updates += 1
            self._remember_read_error(err)
            self.update_interval = timedelta(
                seconds=min(
                    self.poll_interval * 2 ** min(self.consecutive_failures - 1, 4),
                    MAX_POLL_INTERVAL,
                )
            )
            if (
                isinstance(err, (TransientConnectionError, TimeoutError))
                and self._cache_allowed
                and self.data is not None
            ):
                remaining = CACHE_GRACE_SECONDS - (
                    monotonic() - self._last_success_monotonic
                )
                if remaining > 0:
                    self._using_cached_data = True
                    if self._cancel_expiry is None:
                        self._cancel_expiry = async_call_later(
                            self.hass, remaining, self._async_expire_cache
                        )
                    return self.data
            self._invalidate_cache()
            raise UpdateFailed(str(err)) from err
        finally:
            self._active_poll = None

    async def async_shutdown(self):
        """Cancel delayed expiry and any read retry when unloading."""
        self._cancel_cache_expiry()
        await super().async_shutdown()
        if (task := self._active_poll) and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
                # A write is never retried, even if its confirmation read fails.
                self.async_set_updated_data(
                    self._record_success(await self._async_read())
                )
        except _READ_ERRORS as err:
            self._invalidate_cache()
            self.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(
                f"Unable to communicate with the light: {err}"
            ) from err
        except HomeAssistantError as err:
            self._invalidate_cache()
            self.async_set_update_error(UpdateFailed(str(err)))
            raise
