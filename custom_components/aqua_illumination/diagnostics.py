"""Downloadable diagnostics without IP addresses or device identifiers."""

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_ENABLE_CONTROL


async def async_get_config_entry_diagnostics(hass, entry):
    """Return the cached snapshot; diagnostics never query or change the light."""
    coordinator = entry.runtime_data
    return {
        "configuration": async_redact_data(dict(entry.data), {"host", "name"}),
        "options": async_redact_data(dict(entry.options), {"host"}),
        "manual_control_enabled": entry.options.get(CONF_ENABLE_CONTROL, False),
        "last_update_success": coordinator.last_update_success,
        "last_error_type": (
            type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None
        ),
        "firmware": coordinator.api.firmware_version,
        "connection": coordinator.connection_diagnostics,
        "last_successful_update": (
            coordinator.last_successful_update.isoformat()
            if coordinator.last_successful_update
            else None
        ),
        "state": coordinator.data,
        "power_is_estimated": True,
    }
