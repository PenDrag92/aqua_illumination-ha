"""Test against real Home Assistant, with a simulated lamp only."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aqua_illumination.aquaipy.aquaipy import AquaIPy, Response
from custom_components.aqua_illumination.const import DOMAIN

MAC = "00:11:22:33:44:55"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def lamp():
    api = MagicMock(spec=AquaIPy)
    api.mac_addr = MAC
    api.name = "Reef"
    api.firmware_version = "2.5.1"
    state = {"colors": {"blue": 150.0, "white": 20.0}, "schedule": True, "power": 19.0}
    api.async_connect = AsyncMock()
    api.async_get_state = AsyncMock(side_effect=lambda: deepcopy(state))

    async def set_colors(colors):
        state["colors"] = deepcopy(colors)
        return Response.Success

    async def set_schedule(enabled):
        state["schedule"] = enabled
        return Response.Success

    api.async_set_colors_brightness = AsyncMock(side_effect=set_colors)
    api.async_set_schedule_state = AsyncMock(side_effect=set_schedule)
    with (
        patch("custom_components.aqua_illumination.AquaIPy", return_value=api),
        patch(
            "custom_components.aqua_illumination.config_flow.AquaIPy", return_value=api
        ),
    ):
        yield api


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Reef",
        unique_id=MAC,
        data={"host": "192.0.2.10", "name": "Reef"},
    )
    config_entry.add_to_hass(hass)
    return config_entry
