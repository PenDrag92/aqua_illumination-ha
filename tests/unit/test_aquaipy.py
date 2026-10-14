"""Offline regression tests for the Wi-Fi protocol and HD power checks."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aquaipy.aquaipy import AquaIPy, HDDevice, Response
from aquaipy.error import (
    ConnError,
    FirmwareError,
    MustBeParentError,
    TransientConnectionError,
)

MAC = "00:11:22:33:44:55"
IDENTITY = {
    "response_code": 0,
    "serial_number": MAC,
    "firmware": "2.5.1",
    "product": "Hydra 26HD",
    "parent": "",
}
POWER = {
    "serial_number": MAC,
    "normal": {"blue": 10000, "white": 20000},
    "hd": {"blue": 20000, "white": 30000},
    "max_power": 40000,
}


class Reply:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        if isinstance(self.data, BaseException):
            raise self.data
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return deepcopy(self.data)


def make_api(*responses):
    session = MagicMock()
    session.request.side_effect = [Reply(data) for data in responses]
    session.close = AsyncMock()
    return AquaIPy(session=session), session


async def connect(*extra, power=None, identity=None):
    api, session = make_api(
        IDENTITY if identity is None else identity,
        {"response_code": 0, "devices": power if power is not None else [POWER]},
        *extra,
    )
    await api.async_connect("192.0.2.10")
    return api, session


async def test_snapshot_reads_once_and_preserves_hd():
    child = deepcopy(POWER)
    child["serial_number"] = "child"
    api, session = await connect(
        {"response_code": 0, "blue": 1500, "white": 500},
        {"response_code": 0, "enable": True},
        power=[POWER, child],
    )
    assert await api.async_get_state() == {
        "colors": {"blue": 150.0, "white": 50.0},
        "schedule": True,
        "power": 50.0,
        "device_count": 2,
    }
    calls = session.request.call_args_list
    assert len(calls) == 4
    assert all(call.args[0] == "GET" for call in calls)
    assert all(call.kwargs["timeout"].total == 10 for call in calls)


@pytest.mark.parametrize("value", [None, [], {}, {"response_code": 1}, "garbage"])
async def test_invalid_api_envelope_fails(value):
    api, _ = make_api(value)
    with pytest.raises(ConnError):
        await api.async_connect("192.0.2.10")


@pytest.mark.parametrize(
    "error", [TimeoutError(), aiohttp.ClientConnectionError(), ValueError()]
)
async def test_transport_errors_are_reported(error):
    api, _ = make_api(error)
    with pytest.raises(ConnError):
        await api.async_connect("192.0.2.10")
    assert api.base_path is None


async def test_cancellation_propagates():
    api, _ = make_api(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await api.async_connect("192.0.2.10")


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (TimeoutError(), True),
        (aiohttp.ClientConnectionError(), True),
        (aiohttp.ClientPayloadError(), True),
        (ValueError("bad JSON"), False),
        (aiohttp.ClientResponseError(MagicMock(), (), status=400), False),
        (aiohttp.ClientResponseError(MagicMock(), (), status=401), False),
        (aiohttp.ClientResponseError(MagicMock(), (), status=408), True),
        (aiohttp.ClientResponseError(MagicMock(), (), status=429), True),
        (aiohttp.ClientResponseError(MagicMock(), (), status=503), True),
        (aiohttp.ContentTypeError(MagicMock(), (), status=200), False),
    ],
)
async def test_only_transport_and_temporary_http_errors_are_retryable(error, transient):
    api, session = make_api(error)
    with pytest.raises(ConnError) as caught:
        await api.async_connect("192.0.2.10")
    assert isinstance(caught.value, TransientConnectionError) == transient
    assert caught.value.endpoint
    session.request.assert_called_once()


async def test_transport_error_never_replays_a_write():
    api, session = await connect(TimeoutError())
    with pytest.raises(TransientConnectionError):
        await api.async_set_colors_brightness({"blue": 50, "white": 20})
    assert len(session.request.call_args_list) == 3
    assert session.request.call_args_list[-1].args[0] == "POST"


@pytest.mark.parametrize("firmware", ["1.9.9", "2.5.2", "3.0.0", None, "broken"])
async def test_unsupported_firmware_is_not_silently_accepted(firmware):
    with pytest.raises(FirmwareError):
        await connect(identity={**IDENTITY, "firmware": firmware})


async def test_child_cannot_be_configured():
    with pytest.raises(MustBeParentError):
        await connect(identity={**IDENTITY, "parent": "parent"})


@pytest.mark.parametrize("devices", [[], [{**POWER, "serial_number": "child"}]])
async def test_missing_parent_calibration_fails(devices):
    with pytest.raises(ConnError):
        await connect(power=devices)


@pytest.mark.parametrize(
    "colors",
    [
        {"blue": 1000},
        {"blue": 1000, "white": 500, "unknown": 1},
        {"blue": -1, "white": 500},
        {"blue": 2001, "white": 500},
        {"blue": float("nan"), "white": 500},
        {"blue": "10", "white": 500},
    ],
)
async def test_bad_channel_data_fails(colors):
    api, _ = await connect({"response_code": 0, **colors})
    with pytest.raises(ConnError):
        await api.async_get_colors_brightness()


async def test_failed_power_read_is_not_zero_watts():
    api, _ = await connect({"response_code": 1})
    with pytest.raises(ConnError):
        await api.async_get_current_power_draw()


@pytest.mark.parametrize("schedule", [None, 2, "false", [], {}])
async def test_invalid_schedule_is_not_reported_as_off(schedule):
    api, _ = await connect({"response_code": 0, "enable": schedule})
    with pytest.raises(ConnError):
        await api.async_get_schedule_state()


@pytest.mark.parametrize(
    ("percent", "intensity", "milliwatts"),
    [
        (0, 0, 0),
        (50, 500, 5000),
        (100, 1000, 10000),
        (150, 1500, 15000),
        (200, 2000, 20000),
    ],
)
def test_hd_conversion_boundaries(percent, intensity, milliwatts):
    device = HDDevice(POWER, MAC)
    assert device.convert_to_intensity("blue", percent) == intensity
    assert device.convert_to_percentage("blue", intensity) == percent
    assert device.convert_to_mw("blue", intensity) == milliwatts


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, 201])
async def test_invalid_brightness_never_writes(value):
    api, session = await connect()
    session.request.reset_mock(side_effect=True)
    result = await api.async_set_colors_brightness({"blue": value, "white": 0})
    assert result == Response.InvalidBrightnessValue
    session.request.assert_not_called()


async def test_parent_power_limit_never_writes():
    api, session = await connect()
    session.request.reset_mock(side_effect=True)
    assert (
        await api.async_set_colors_brightness({"blue": 200, "white": 150})
        == Response.PowerLimitExceeded
    )
    session.request.assert_not_called()


async def test_child_power_limit_never_writes():
    child = {**POWER, "serial_number": "child", "max_power": 10000}
    api, session = await connect(power=[POWER, child])
    session.request.reset_mock(side_effect=True)
    assert (
        await api.async_set_colors_brightness({"blue": 100, "white": 100})
        == Response.PowerLimitExceeded
    )
    session.request.assert_not_called()


async def test_exact_power_limit_is_allowed_and_schedule_is_untouched():
    api, session = await connect({"response_code": 0})
    assert (
        await api.async_set_colors_brightness({"blue": 200, "white": 100})
        == Response.Success
    )
    last = session.request.call_args
    assert last.args == ("POST", "http://192.0.2.10/api/colors")
    assert last.kwargs["json"] == {"blue": 2000, "white": 1000}
    assert not any(call.args[0] == "PUT" for call in session.request.call_args_list)


async def test_rejected_write_raises():
    api, _ = await connect({"response_code": 1})
    with pytest.raises(ConnError):
        await api.async_set_colors_brightness({"blue": 10, "white": 20})


async def test_shared_session_is_never_closed():
    api, session = make_api()
    await api.async_close()
    session.close.assert_not_awaited()


async def test_owned_session_is_closed():
    api = AquaIPy()
    await api.async_close()
    assert api._session.closed
