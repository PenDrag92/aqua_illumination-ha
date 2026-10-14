"""The optional daily-plan reads must never change or take down live lighting."""

import asyncio
from unittest.mock import patch

import pytest

from custom_components.aqua_illumination.aquaipy.error import ConnError

MODULE = "custom_components.aqua_illumination.coordinator"
PLAN = "sensor.reef_schedule_plan"
POWER = "sensor.reef_estimated_led_power"


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_plan_reads_infrequently_and_button_forces_read(hass, entry, lamp):
    with patch(f"{MODULE}.monotonic", return_value=1000) as clock:
        coordinator = await setup(hass, entry)
        lamp.async_get_schedule_plan.assert_awaited_once()
        state = hass.states.get(PLAN)
        assert state.state == "Loaded"
        assert state.attributes["base_schedule"]
        assert state.attributes["refresh_interval_seconds"] == 1800
        for elapsed in (30, 60, 300, 1799):
            clock.return_value = 1000 + elapsed
            await coordinator.async_refresh()
        lamp.async_get_schedule_plan.assert_awaited_once()
        clock.return_value = 2801
        await coordinator.async_refresh()
        assert lamp.async_get_schedule_plan.await_count == 2
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.reef_refresh_schedule"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert lamp.async_get_schedule_plan.await_count == 3
        lamp.async_set_colors_brightness.assert_not_awaited()
        lamp.async_set_schedule_state.assert_not_awaited()


@pytest.mark.parametrize("initial_failure", [False, True])
async def test_plan_failure_preserves_live_readings_and_retries_slowly(
    hass, entry, lamp, initial_failure
):
    error = ConnError("unsupported plan", "192.0.2.10", endpoint="schedule")
    if initial_failure:
        lamp.async_get_schedule_plan.side_effect = error
    with patch(f"{MODULE}.monotonic", return_value=1000) as clock:
        coordinator = await setup(hass, entry)
        lamp.async_get_schedule_plan.side_effect = error
        clock.return_value = 2801
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert not coordinator.data_stale
        assert coordinator.failed_updates == 0
        assert hass.states.get(POWER).state == "19.0"
        plan = hass.states.get(PLAN)
        assert plan.state == ("unavailable" if initial_failure else "Cached")
        if not initial_failure:
            assert plan.attributes["channels"]
            assert plan.attributes["cached"]
            assert plan.attributes["last_read_error"]["endpoint"] == "schedule"
        count = lamp.async_get_schedule_plan.await_count
        clock.return_value = 3000
        await coordinator.async_refresh()
        assert lamp.async_get_schedule_plan.await_count == count
        lamp.async_get_schedule_plan.side_effect = None
        clock.return_value = 3102
        await coordinator.async_refresh()
        assert hass.states.get(PLAN).state == "Loaded"
        assert coordinator.schedule_last_error is None


async def test_plan_deadline_does_not_discard_successful_brightness(hass, entry, lamp):
    async def slow_plan():
        await asyncio.Event().wait()

    lamp.async_get_schedule_plan.side_effect = slow_plan
    with patch(f"{MODULE}.SCHEDULE_READ_TIMEOUT", 0.01):
        coordinator = await setup(hass, entry)
    assert coordinator.last_update_success
    assert coordinator.failed_updates == 0
    assert coordinator.schedule_last_error["type"] == "TimeoutError"
    assert hass.states.get(POWER).state == "19.0"
    assert hass.states.get(PLAN).state == "unavailable"


async def test_unload_cancels_pending_plan_read(hass, entry, lamp):
    coordinator = await setup(hass, entry)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_plan():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    lamp.async_get_schedule_plan.side_effect = slow_plan
    coordinator._next_schedule_read = 0
    task = asyncio.create_task(coordinator.async_refresh())
    await asyncio.wait_for(started.wait(), 1)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert task.done()
    assert cancelled.is_set()
    assert not coordinator._active_polls
