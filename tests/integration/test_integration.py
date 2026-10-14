"""Regression coverage for configuration, retries, controls and unload."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from custom_components.aqua_illumination.aquaipy.aquaipy import Response
from custom_components.aqua_illumination.aquaipy.error import (
    ConnError,
    FirmwareError,
    MustBeParentError,
)
from custom_components.aqua_illumination.const import DOMAIN
from custom_components.aqua_illumination.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.aqua_illumination.light import AquaIlluminationLight


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_read_only_setup_poll_unload(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    states = hass.states.async_all()
    assert states
    assert all(state.domain == "sensor" for state in states)
    assert any(state.state == "150.0" for state in states)
    assert any(state.state == "Scheduled" for state in states)
    await coordinator.async_refresh()
    lamp.async_set_colors_brightness.assert_not_awaited()
    lamp.async_set_schedule_state.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="read only"):
        await coordinator.async_set_channel("blue", 10)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED


async def test_unreachable_setup_retries(hass, entry, lamp):
    lamp.async_connect.side_effect = ConnError("offline", "192.0.2.10")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.SETUP_RETRY
    lamp.async_set_colors_brightness.assert_not_awaited()


async def test_invalid_reply_marks_measurements_unavailable_and_recovers(
    hass, entry, lamp
):
    coordinator = await setup(hass, entry)
    original = lamp.async_get_state.side_effect
    lamp.async_get_state.side_effect = ConnError("offline", "192.0.2.10")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.reef_estimated_led_power").state == "unavailable"
    assert hass.states.get("sensor.reef_connection_status").state == "Disconnected"
    lamp.async_get_state.side_effect = original
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert lamp.async_connect.await_count == 2
    assert all(state.state != "unavailable" for state in hass.states.async_all())


async def test_wrong_device_after_ip_reassignment_is_rejected(hass, entry, lamp):
    lamp.mac_addr = "different-light"
    assert not await hass.config_entries.async_setup(entry.entry_id)
    lamp.async_get_state.assert_not_awaited()
    lamp.async_set_colors_brightness.assert_not_awaited()


async def test_duplicate_flow_aborts_instead_of_unknown_error(hass, entry, lamp):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"host": "192.0.2.10", "name": "Duplicate"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_new_flow_uses_ha_session_and_defaults_to_read_only(hass, lamp):
    with patch(
        "custom_components.aqua_illumination.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={"host": " 192.0.2.10 ", "name": "Reef"},
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "192.0.2.10"
    assert not result["result"].options.get("enable_control", False)
    lamp.async_set_colors_brightness.assert_not_awaited()
    lamp.async_set_schedule_state.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (ConnError("offline", "192.0.2.10"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (FirmwareError("unsupported", "9.0.0"), "unsupported_firmware"),
        (MustBeParentError("child", "parent"), "not_parent"),
    ],
)
async def test_flow_errors_are_translated(hass, lamp, error, key):
    lamp.async_connect.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"host": "192.0.2.10", "name": "Reef"},
    )
    assert result["errors"] == {"base": key}


async def test_invalid_host_does_not_contact_network(hass, lamp):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"host": "http://example.invalid/path", "name": "Reef"},
    )
    assert result["errors"] == {"base": "invalid_host"}
    lamp.async_connect.assert_not_awaited()


async def test_options_validate_ip_and_reload(hass, entry, lamp):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"host": "192.0.2.20", "enable_control": False},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.runtime_data.host == "192.0.2.20"
    assert entry.state == ConfigEntryState.LOADED
    lamp.async_set_colors_brightness.assert_not_awaited()


async def test_options_reject_different_lamp(hass, entry, lamp):
    lamp.mac_addr = "other-light"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"host": "192.0.2.20", "enable_control": False},
    )
    assert result["errors"] == {"base": "wrong_device"}
    assert "host" not in entry.options


async def test_diagnostics_redact_private_data(hass, entry, lamp):
    await setup(hass, entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)
    assert "192.0.2.10" not in serialized
    assert entry.unique_id not in serialized
    assert "Reef" not in serialized
    assert diagnostics["state"]["colors"]["blue"] == 150


async def test_enabled_controls_are_serialized_and_preserve_other_hd_channels(
    hass, entry, lamp
):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    await asyncio.gather(
        coordinator.async_set_channel("white", 40),
        coordinator.async_set_channel("blue", 80),
    )
    assert coordinator.data["colors"] == {"blue": 80, "white": 40}
    assert lamp.async_set_colors_brightness.call_args_list[0].args[0]["blue"] == 150
    lamp.async_set_schedule_state.assert_not_awaited()


async def test_light_clamps_display_but_sensor_keeps_hd(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    light = AquaIlluminationLight(coordinator, "blue")
    assert light.brightness == 255
    assert isinstance(light.brightness, int)
    assert coordinator.data["colors"]["blue"] == 150


async def test_rejected_command_does_not_report_success(hass, entry, lamp):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    lamp.async_set_colors_brightness = AsyncMock(
        return_value=Response.PowerLimitExceeded
    )
    with pytest.raises(HomeAssistantError, match="PowerLimitExceeded"):
        await coordinator.async_set_channel("white", 100)
    assert coordinator.data["colors"]["white"] == 20


async def test_disabling_controls_unloads_previously_loaded_platforms(
    hass, entry, lamp
):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    await setup(hass, entry)
    assert hass.states.async_all("light")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"host": "192.0.2.10", "enable_control": False},
    )
    await hass.async_block_till_done()
    # HA keeps registry-backed placeholders after unloading these platforms.
    controls = hass.states.async_all("light") + hass.states.async_all("switch")
    assert controls
    assert all(state.state == "unavailable" for state in controls)
    assert not entry.runtime_data.control_enabled
    with pytest.raises(HomeAssistantError, match="read only"):
        await entry.runtime_data.async_set_channel("blue", 10)
    lamp.async_set_colors_brightness.assert_not_awaited()
    lamp.async_set_schedule_state.assert_not_awaited()
    assert entry.state == ConfigEntryState.LOADED


async def test_light_service_preserves_hd_and_updates_all_sensors(hass, entry, lamp):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.reef_white", "brightness": 102},
        blocking=True,
    )
    assert coordinator.data["colors"] == {"blue": 150, "white": 40}
    assert hass.states.get("light.reef_white").attributes["brightness"] == 102
    lamp.async_set_schedule_state.assert_not_awaited()


async def test_schedule_service_only_changes_schedule_on_command(hass, entry, lamp):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    lamp.async_set_schedule_state.assert_not_awaited()
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.reef_scheduled_mode"},
        blocking=True,
    )
    assert not coordinator.data["schedule"]
    lamp.async_set_schedule_state.assert_awaited_once_with(False)
    lamp.async_set_colors_brightness.assert_not_awaited()


async def test_transport_failure_during_write_marks_state_unavailable(
    hass, entry, lamp
):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    lamp.async_set_colors_brightness.side_effect = ConnError("offline", "192.0.2.10")
    with pytest.raises(HomeAssistantError, match="Unable to communicate"):
        await coordinator.async_set_channel("white", 40)
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.reef_estimated_led_power").state == "unavailable"
    assert hass.states.get("sensor.reef_connection_status").state == "Disconnected"
