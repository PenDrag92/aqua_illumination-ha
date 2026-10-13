"""Aqua Illumination Wi-Fi lights, with read-only setup by default."""

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .aquaipy import AquaIPy
from .const import CONF_ENABLE_CONTROL, DOMAIN
from .coordinator import AquaIlluminationCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Fetch a complete snapshot before adding entities."""
    host = entry.options.get("host", entry.data["host"])
    api = AquaIPy(
        entry.data.get("name", entry.title), session=async_get_clientsession(hass)
    )
    coordinator = AquaIlluminationCoordinator(hass, entry, api, host)
    await coordinator.async_config_entry_first_refresh()
    coordinator.platforms = [Platform.SENSOR]
    if entry.options.get(CONF_ENABLE_CONTROL, False):
        coordinator.platforms.extend([Platform.LIGHT, Platform.SWITCH])
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the platforms that were actually loaded, even after options change."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
