"""Exercise short Wi-Fi gaps, hard failures and recovery with a simulated lamp."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.aqua_illumination.aquaipy.error import (
    ConnError,
    FirmwareError,
    TransientConnectionError,
)
from custom_components.aqua_illumination.diagnostics import (
    async_get_config_entry_diagnostics,
)

MODULE = "custom_components.aqua_illumination.coordinator"
POWER = "sensor.reef_estimated_led_power"
STATUS = "sensor.reef_connection_status"
STAMP = "sensor.reef_last_successful_update"


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Advance protocol attempts without sleeping; the unload test overrides this."""
    with patch(f"{MODULE}.READ_RETRY_DELAY", 0):
        yield


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def offline():
    return TransientConnectionError("offline", "192.0.2.10", endpoint="colors")


async def test_single_failed_read_retries_once_without_availability_flap(
    hass, entry, lamp
):
    coordinator = await setup(hass, entry)
    state = dict(coordinator.data)
    lamp.async_get_state.side_effect = [offline(), state]
    lamp.async_get_state.reset_mock()
    await coordinator.async_refresh()
    assert lamp.async_get_state.await_count == 2
    assert lamp.async_connect.await_count == 2
    assert coordinator.read_retries == 1
    assert coordinator.failed_updates == 0
    assert coordinator.connection_status == "Connected"
    assert hass.states.get(POWER).state == "19.0"
    assert not hass.states.get(POWER).attributes["data_stale"]
    lamp.async_set_colors_brightness.assert_not_awaited()
    lamp.async_set_schedule_state.assert_not_awaited()


@pytest.mark.parametrize("gap_seconds", [13, 36, 88])
async def test_short_outage_keeps_explicitly_stale_snapshot_then_recovers(
    hass, entry, lamp, gap_seconds
):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    with patch(f"{MODULE}.monotonic", return_value=1000) as clock:
        coordinator = await setup(hass, entry)
        last_success = coordinator.last_successful_update
        original = lamp.async_get_state.side_effect
        clock.return_value += gap_seconds
        lamp.async_get_state.side_effect = offline()
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data_stale
        assert coordinator.last_successful_update == last_success
        assert hass.states.get(STAMP).state == last_success.isoformat()
        power = hass.states.get(POWER)
        assert power.state == "19.0"
        assert power.attributes["data_stale"]
        assert power.attributes["last_successful_update"] == last_success.isoformat()
        assert power.attributes["detected_lights"] == 3
        assert power.attributes["child_lights"] == 2
        assert hass.states.get(STATUS).state == "Reconnecting"
        assert all(
            state.state == "unavailable"
            for state in hass.states.async_all("light")
            + hass.states.async_all("switch")
        )
        lamp.async_get_state.side_effect = original
        await coordinator.async_refresh()
    assert not coordinator.data_stale
    assert coordinator.consecutive_failures == 0
    assert coordinator.update_interval == timedelta(seconds=30)
    assert coordinator._cancel_expiry is None
    assert hass.states.get(STATUS).state == "Connected"
    assert not hass.states.get(POWER).attributes["data_stale"]
    assert hass.states.get("light.reef_white").state != "unavailable"
    lamp.async_set_colors_brightness.assert_not_awaited()
    lamp.async_set_schedule_state.assert_not_awaited()


async def test_persistent_failure_backs_off_without_repeated_fast_retries(
    hass, entry, lamp
):
    coordinator = await setup(hass, entry)
    lamp.async_get_state.side_effect = offline()
    lamp.async_get_state.reset_mock()
    for expected_interval in (30, 60, 120, 120, 120):
        await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(seconds=expected_interval)
    assert lamp.async_get_state.await_count == 6  # One retry, then single attempts.
    assert coordinator.read_retries == 1
    assert coordinator.failed_updates == 5
    assert coordinator.last_read_error["endpoint"] == "colors"


async def test_expiry_timer_marks_unavailable_without_another_poll(
    hass, entry, lamp, freezer
):
    """Expiry is tied to the successful read, even when the next poll is delayed."""
    coordinator = await setup(hass, entry)
    stamp = coordinator.last_successful_update
    lamp.async_get_state.side_effect = offline()
    # Do not let scheduled polling participate in this test.
    with patch.object(coordinator, "_schedule_refresh"):
        freezer.tick(90)
        await coordinator.async_refresh()
        assert hass.states.get(STATUS).state == "Reconnecting"
        reads = lamp.async_get_state.await_count
        freezer.tick(31)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert lamp.async_get_state.await_count == reads
    assert not coordinator.last_update_success
    assert hass.states.get(POWER).state == "unavailable"
    assert hass.states.get(STATUS).state == "Disconnected"
    assert coordinator.last_successful_update == stamp
    assert hass.states.get(STAMP).state == stamp.isoformat()


async def test_snapshot_older_than_grace_is_not_republished(hass, entry, lamp):
    with patch(f"{MODULE}.monotonic", return_value=1000) as clock:
        coordinator = await setup(hass, entry)
        clock.return_value += 121
        lamp.async_get_state.side_effect = offline()
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator._cancel_expiry is None
    assert hass.states.get(POWER).state == "unavailable"


@pytest.mark.parametrize(
    "error",
    [ConnError("invalid data", "192.0.2.10"), FirmwareError("unsupported", "9.0.0")],
)
async def test_hard_failure_does_not_retry_or_keep_cached_values(
    hass, entry, lamp, error
):
    coordinator = await setup(hass, entry)
    lamp.async_get_state.reset_mock()
    lamp.async_get_state.side_effect = error
    await coordinator.async_refresh()
    lamp.async_get_state.assert_awaited_once()
    assert coordinator.read_retries == 0
    assert not coordinator.last_update_success
    assert coordinator._cancel_expiry is None


async def test_reconnect_to_wrong_light_immediately_discards_cache(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    lamp.async_get_state.side_effect = offline()
    lamp.mac_addr = "another-light"
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator._cancel_expiry is None
    assert hass.states.get(STATUS).state == "Disconnected"
    assert "different light" in str(coordinator.last_exception)
    lamp.async_set_colors_brightness.assert_not_awaited()


@pytest.mark.parametrize("failure_after_write", [False, True])
async def test_uncertain_write_is_never_retried_or_hidden_by_cache(
    hass, entry, lamp, failure_after_write
):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    coordinator = await setup(hass, entry)
    snapshot = dict(coordinator.data)
    if failure_after_write:
        lamp.async_get_state.side_effect = [snapshot, offline()]
    else:
        lamp.async_set_colors_brightness.side_effect = offline()
    with pytest.raises(HomeAssistantError, match="Unable to communicate"):
        await coordinator.async_set_channel("white", 40)
    lamp.async_set_colors_brightness.assert_awaited_once()
    assert not coordinator.last_update_success
    assert hass.states.get(POWER).state == "unavailable"
    lamp.async_get_state.side_effect = offline()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator._cancel_expiry is None
    lamp.async_set_colors_brightness.assert_awaited_once()


async def test_unload_cancels_in_flight_retry(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    first_attempt = asyncio.Event()

    async def fail():
        first_attempt.set()
        raise offline()

    lamp.async_get_state.side_effect = fail
    lamp.async_get_state.reset_mock()
    with patch(f"{MODULE}.READ_RETRY_DELAY", 30):
        refresh = asyncio.create_task(coordinator.async_refresh())
        await first_attempt.wait()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert refresh.done()
    lamp.async_get_state.assert_awaited_once()
    assert coordinator._cancel_expiry is None


async def test_unload_cancels_cache_expiry(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    lamp.async_get_state.side_effect = offline()
    await coordinator.async_refresh()
    assert coordinator._cancel_expiry is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._cancel_expiry is None


async def test_poll_option_preserves_ip_and_enabled_controls(hass, entry, lamp):
    hass.config_entries.async_update_entry(entry, options={"enable_control": True})
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"host": "192.0.2.10", "enable_control": True, "poll_interval": 45},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.runtime_data.host == "192.0.2.10"
    assert entry.runtime_data.control_enabled
    assert entry.runtime_data.update_interval == timedelta(seconds=45)
    assert hass.states.get("sensor.reef_detected_lights").state == "3"
    lamp.async_set_colors_brightness.assert_not_awaited()


@pytest.mark.parametrize("value", [0, 9, 61, "invalid"])
async def test_invalid_poll_option_does_not_contact_lamp(hass, entry, lamp, value):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "host": "192.0.2.10",
            "enable_control": False,
            "poll_interval": value,
        },
    )
    assert result["errors"] == {"base": "invalid_poll_interval"}
    lamp.async_connect.assert_not_awaited()


async def test_connection_diagnostics_use_cached_data_and_redact_error(
    hass, entry, lamp
):
    coordinator = await setup(hass, entry)
    lamp.async_get_state.side_effect = offline()
    await coordinator.async_refresh()
    lamp.async_get_state.reset_mock()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["connection"]["data_stale"]
    assert diagnostics["connection"]["last_read_error"]["endpoint"] == "colors"
    assert diagnostics["last_successful_update"]
    assert "192.0.2.10" not in str(diagnostics)
    assert entry.unique_id not in str(diagnostics)
    lamp.async_get_state.assert_not_awaited()
