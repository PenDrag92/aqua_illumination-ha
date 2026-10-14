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


DAILY_SCHEDULE = {
    "response_code": 0,
    "name": "default",
    "ramps": [
        {
            "color": "blue",
            "points": [
                {"time": 0, "intensity": 0},
                {"time": 600, "intensity": 1500},
                {"time": 1200, "intensity": 0},
            ],
        },
        {
            "color": "white",
            "points": [
                {"time": 0, "intensity": 0},
                {"time": 720, "intensity": 2000},
                {"time": 1200, "intensity": 0},
            ],
        },
    ],
}
LAMP_CLOCK = {
    "response_code": 0,
    "year": 2026,
    "month": 9,
    "day": 14,
    "hours": 7,
    "minutes": 58,
    "seconds": 49,
    "gmt_offset": 7200,
}
MODIFIER_OFF = {"response_code": 0, "enable": False}


async def test_daily_plan_is_read_only_and_includes_hd_corners_and_children():
    child = {**POWER, "serial_number": "child"}
    api, session = await connect(
        DAILY_SCHEDULE,
        MODIFIER_OFF,
        MODIFIER_OFF,
        {"response_code": 0, "enable": True},
        LAMP_CLOCK,
        power=[POWER, child],
    )
    plan = await api.async_get_schedule_plan()
    assert plan["lamp_time_at_read"] == "2026-10-14T07:58:49+02:00"
    assert plan["modifiers"] == {"acclimation": False, "lunar": False, "storm": True}
    assert plan["base_schedule"]
    assert plan["device_count"] == 2
    white = {p["minute"]: p["brightness"] for p in plan["channels"]["white"]}
    assert white == {0: 0, 360: 100, 720: 150, 960: 100, 1200: 0, 1440: 0}
    power = {p["minute"]: p["power"] for p in plan["power_curve"]}
    assert power[0] == power[1440] == 0
    assert power[720] == 84  # 12 W blue + 30 W white, on each of two lights.
    assert all(call.args[0] == "GET" for call in session.request.call_args_list)
    endpoints = [
        call.args[1].rsplit("/api/", 1)[1] for call in session.request.call_args_list
    ]
    assert endpoints == [
        "identity",
        "power",
        "schedule",
        "schedule/acclimation",
        "schedule/lunar",
        "schedule/storm",
        "time",
    ]
    assert DAILY_SCHEDULE["ramps"][0]["points"][-1]["time"] == 1200


@pytest.mark.parametrize(
    "points",
    [
        None,
        {},
        [None],
        [{"time": 0}],
        [{"time": -1, "intensity": 0}],
        [{"time": True, "intensity": 0}],
        [{"time": 0.5, "intensity": 0}],
        [{"time": 0, "intensity": True}],
        [{"time": 0, "intensity": -1}],
        [{"time": 0, "intensity": 2001}],
        [{"time": 0, "intensity": float("nan")}],
        [{"time": 0, "intensity": float("inf")}],
        [{"time": 0, "intensity": "50"}],
        [{"time": 10, "intensity": 0}],
        [{"time": 0, "intensity": 0}, {"time": 1440, "intensity": 0}],
        [{"time": 0, "intensity": 0}, {"time": 0, "intensity": 100}],
        [
            {"time": 0, "intensity": 0},
            {"time": 20, "intensity": 100},
            {"time": 10, "intensity": 0},
        ],
    ],
)
async def test_bad_schedule_points_fail_before_modifiers(points):
    schedule = deepcopy(DAILY_SCHEDULE)
    schedule["ramps"][0]["points"] = points
    api, session = await connect(schedule)
    with pytest.raises(ConnError) as error:
        await api.async_get_schedule_plan()
    assert error.value.endpoint == "schedule"
    assert len(session.request.call_args_list) == 3


@pytest.mark.parametrize(
    "ramps",
    [
        None,
        [],
        [None, None],
        [DAILY_SCHEDULE["ramps"][0]] * 2,
        [{"color": "unknown", "points": []}, DAILY_SCHEDULE["ramps"][1]],
        [{"color": [], "points": []}, DAILY_SCHEDULE["ramps"][1]],
    ],
)
async def test_missing_or_duplicate_schedule_channels_fail(ramps):
    api, _ = await connect({**DAILY_SCHEDULE, "ramps": ramps})
    with pytest.raises(ConnError):
        await api.async_get_schedule_plan()


async def test_empty_and_constant_schedule_channels_wrap_at_midnight():
    from aquaipy.schedule import describe_schedule, intensity_at, parse_ramps

    schedule = deepcopy(DAILY_SCHEDULE)
    schedule["ramps"][0]["points"] = []
    schedule["ramps"][1]["points"] = [{"time": 0, "intensity": 100}]
    api, _ = await connect()
    plan = describe_schedule(schedule, api._primary_device, api._calculate_power, 1)
    assert plan["power_curve"] == [
        {"minute": 0, "power": 2},
        {"minute": 1440, "power": 2},
    ]
    schedule["ramps"][1]["points"].append({"time": 1200, "intensity": 900})
    ramps = parse_ramps(schedule, ("blue", "white"))
    assert intensity_at(ramps["white"], 1320) == 500
    assert intensity_at(ramps["white"], 1440) == 100
    assert intensity_at(ramps["white"], 600) == 500


@pytest.mark.parametrize("enabled", [None, 2, "false", [], 0.0])
async def test_unknown_modifier_is_never_assumed_disabled(enabled):
    api, _ = await connect(DAILY_SCHEDULE, {"response_code": 0, "enable": enabled})
    with pytest.raises(ConnError) as error:
        await api.async_get_schedule_plan()
    assert error.value.endpoint == "schedule/acclimation"


@pytest.mark.parametrize(
    "changed",
    [
        {"month": 12},
        {"day": 32},
        {"hours": 24},
        {"seconds": 60},
        {"year": True},
        {"gmt_offset": 86400},
        {"minutes": "58"},
    ],
)
async def test_invalid_lamp_clock_is_rejected(changed):
    api, _ = await connect(
        DAILY_SCHEDULE,
        MODIFIER_OFF,
        MODIFIER_OFF,
        MODIFIER_OFF,
        {**LAMP_CLOCK, **changed},
    )
    with pytest.raises(ConnError) as error:
        await api.async_get_schedule_plan()
    assert error.value.endpoint == "time"


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
