#
#   Copyright 2018 Stephen Mc Gowan <mcclown@gmail.com>
#   Modernized for HA 2026.3 / Python 3.14 by Rasmus Tornefrost
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Module for working with the AquaIllumination range of lights."""

import asyncio
import math
from enum import Enum
from ipaddress import ip_address

import aiohttp

# FIX 1: Use relative imports for vendoring
from .error import ConnError, FirmwareError, MustBeParentError, TransientConnectionError

MIN_SUPPORTED_AI_FIRMWARE_VERSION = "2.0.0"
MAX_SUPPORTED_AI_FIRMWARE_VERSION = "2.5.1"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# FIX 2: Replaced deleted distutils with native version parsing
def _parse_version(version_string):
    """Safely convert a semantic version string to a tuple for comparison."""
    try:
        return tuple(map(int, version_string.split(".")))
    except (AttributeError, TypeError, ValueError):
        return (0, 0, 0)


class Response(Enum):
    """Response codes, for the AquaIPy methods."""

    Success = 0
    Error = 1
    NoSuchColour = 2
    InvalidBrightnessValue = 3
    PowerLimitExceeded = 4
    AllColorsMustBeSpecified = 5
    InvalidData = 6


class HDDevice:
    """A class for handling the conversion of data for a device."""

    def __init__(self, raw_data, primary_mac_address=None):
        self._primary_mac_address = primary_mac_address
        self._mac_address = raw_data["serial_number"]
        self._mw_norm = {}
        self._mw_hd = {}
        self._max_mw = 0

        for color, value in raw_data["normal"].items():
            self._mw_norm[color] = value

        for color, value in raw_data["hd"].items():
            self._mw_hd[color] = value

        self._max_mw = raw_data["max_power"]

        if (
            not self._mw_norm
            or self._mw_norm.keys() != self._mw_hd.keys()
            or not _is_number(self._max_mw)
            or self._max_mw <= 0
            or any(
                not _is_number(value) or value <= 0 for value in self._mw_norm.values()
            )
            or any(
                not _is_number(value) or value < self._mw_norm[color]
                for color, value in self._mw_hd.items()
            )
        ):
            raise ValueError("Invalid device power calibration")

    @property
    def is_primary(self):
        return self._primary_mac_address == self._mac_address

    @property
    def mac_address(self):
        return self._mac_address

    @property
    def max_mw(self):
        return self._max_mw

    def convert_to_intensity(self, color, percentage):
        if not _is_number(percentage) or percentage < 0:
            raise ValueError("Percentage must be greater than 0")
        elif 0 <= percentage <= 100:
            return round(percentage * 10)
        else:
            max_percentage = ((self._mw_hd[color]) / self._mw_norm[color]) * 100
            if percentage > max_percentage:
                raise ValueError(
                    f"Percentage for {color} must be between 0 and {max_percentage}"
                )

            hd_percentage = percentage - 100
            hd_brightness_value = (hd_percentage / (max_percentage - 100)) * 1000
            return round(hd_brightness_value + 1000)

    def convert_to_percentage(self, color, intensity):
        if not _is_number(intensity) or intensity < 0 or intensity > 2000:
            raise ValueError("intensity must be between 0 and 2000")
        elif intensity <= 1000:
            return intensity / 10
        else:
            max_hd_percentage = (
                self._mw_hd[color] - self._mw_norm[color]
            ) / self._mw_norm[color]
            hd_in_use = (intensity - 1000) / 1000
            return 100 + (max_hd_percentage * hd_in_use * 100)

    def convert_to_mw(self, color, intensity):
        if not _is_number(intensity) or intensity < 0 or intensity > 2000:
            raise ValueError("intensity must be between 0 and 2000")
        elif intensity <= 1000:
            return self._mw_norm[color] * (intensity / 1000)
        else:
            hd_in_use = (intensity - 1000) / 1000
            hd_mw_in_use = hd_in_use * (self._mw_hd[color] - self._mw_norm[color])
            return self._mw_norm[color] + hd_mw_in_use


class AquaIPy:
    """A class that exposes the AquaIllumination Lights API."""

    def __init__(self, name=None, session=None, loop=None):
        self._host = None
        self._base_path = None
        self._mac_addr = None
        self._name = name
        self._product_type = None
        self._firmware_version = None
        self._primary_device = None
        self._other_devices = []

        self._loop = loop
        self._loop_is_local = False

        if self._loop is None:
            try:
                # FIX 3: Updated asyncio loop fetching
                self._loop = asyncio.get_running_loop()
                self._loop_is_local = False
            except RuntimeError:
                self._create_new_event_loop()

        if self._loop.is_closed():
            self._create_new_event_loop()

        if session is None:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
            self._session_is_local = True
        else:
            self._session = session
            self._session_is_local = False

    def connect(self, host, check_firmware_support=True):
        return self._loop.run_until_complete(
            self.async_connect(host, check_firmware_support)
        )

    async def async_connect(self, host, check_firmware_support=True):
        self._host = host
        self._primary_device = None
        self._other_devices = []
        try:
            address = str(ip_address(host))
            address = f"[{address}]" if ":" in address else address
            self._base_path = f"http://{address}/api"
            await self._async_setup_device_details(check_firmware_support)
        except (ConnError, FirmwareError, MustBeParentError):
            self._base_path = None
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            self._base_path = None
            raise ConnError("Invalid device metadata", host) from err

    async def async_close(self):
        """Close only sessions owned by this library, never HA's shared session."""
        self._base_path = None
        if self._session_is_local:
            await self._session.close()

    def close(self):
        if self._loop.is_running():
            raise RuntimeError("Use await async_close() from an async context")
        self._loop.run_until_complete(self.async_close())
        if self._loop_is_local:
            self._loop.close()

    @property
    def mac_addr(self):
        return self._mac_addr

    @property
    def name(self):
        return self._name

    @property
    def product_type(self):
        return self._product_type

    @property
    def supported_firmware(self):
        # FIX 5: Uses new tuple-based version check
        current_v = _parse_version(self._firmware_version)
        min_v = _parse_version(MIN_SUPPORTED_AI_FIRMWARE_VERSION)
        max_v = _parse_version(MAX_SUPPORTED_AI_FIRMWARE_VERSION)
        return min_v <= current_v <= max_v

    @property
    def base_path(self):
        return self._base_path

    @property
    def firmware_version(self):
        return self._firmware_version

    def _validate_connection(self):
        if self._base_path is None:
            raise ConnError("Error connecting to host", self._host)

    def _create_new_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_is_local = True

    async def _async_request(self, method, endpoint, **kwargs):
        """Bound each request and reject HTTP errors or malformed API envelopes."""
        self._validate_connection()
        try:
            async with self._session.request(
                method,
                f"{self._base_path}/{endpoint}",
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientResponseError as err:
            error_type = (
                TransientConnectionError
                if err.status in (408, 429) or err.status >= 500
                else ConnError
            )
            raise error_type(
                f"HTTP request failed: {endpoint}", self._host, endpoint=endpoint
            ) from err
        except (
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
            TimeoutError,
        ) as err:
            raise TransientConnectionError(
                f"Request failed: {endpoint}", self._host, endpoint=endpoint
            ) from err
        except (aiohttp.ClientError, ValueError) as err:
            raise ConnError(
                f"Invalid response: {endpoint}", self._host, endpoint=endpoint
            ) from err
        if not isinstance(data, dict) or data.get("response_code") != 0:
            raise ConnError(
                f"Invalid or rejected response: {endpoint}",
                self._host,
                endpoint=endpoint,
            )
        return data

    async def _async_setup_device_details(self, check_firmware_support):
        data = await self._async_request("GET", "identity")
        self._mac_addr = data["serial_number"]
        self._firmware_version = data["firmware"]
        self._product_type = data["product"]
        if not isinstance(self._mac_addr, str) or not self._mac_addr:
            raise ValueError("Missing device identity")
        if check_firmware_support and not self.supported_firmware:
            raise FirmwareError("Unsupported light firmware", self._firmware_version)
        if data["parent"] != "":
            raise MustBeParentError("Connect to the parent light", data["parent"])
        await self._async_get_devices()

    async def _async_get_devices(self):
        data = await self._async_request("GET", "power")
        self._primary_device = None
        self._other_devices = []
        for raw_device in data["devices"]:
            device = HDDevice(raw_device, self.mac_addr)
            if device.is_primary:
                if self._primary_device is not None:
                    raise ValueError("Duplicate primary device")
                self._primary_device = device
            else:
                self._other_devices.append(device)
        if self._primary_device is None:
            raise ConnError("Power data is missing the parent light", self._host)
        channels = self._primary_device._mw_norm.keys()
        if any(device._mw_norm.keys() != channels for device in self._other_devices):
            raise ConnError("Paired lights have incompatible channels", self._host)

    async def _async_get_brightness(self):
        data = dict(await self._async_request("GET", "colors"))
        del data["response_code"]
        if (
            self._primary_device is None
            or data.keys() != self._primary_device._mw_norm.keys()
            or any(
                not _is_number(value) or not 0 <= value <= 2000
                for value in data.values()
            )
        ):
            raise ConnError("Invalid channel data", self._host)
        return Response.Success, data

    async def _async_set_brightness(self, body):
        await self._async_request("POST", "colors", json=body)
        return Response.Success

    def get_schedule_state(self):
        return self._loop.run_until_complete(self.async_get_schedule_state())

    async def async_get_schedule_state(self):
        data = await self._async_request("GET", "schedule/enable")
        value = data.get("enable")
        if not isinstance(value, (bool, int)) or value not in (0, 1):
            raise ConnError("Invalid schedule state", self._host)
        return bool(value)

    def set_schedule_state(self, enable):
        return self._loop.run_until_complete(self.async_set_schedule_state(enable))

    async def async_set_schedule_state(self, enable):
        if not isinstance(enable, bool):
            return Response.InvalidData
        await self._async_request("PUT", "schedule/enable", json={"enable": enable})
        return Response.Success

    def get_colors(self):
        return self._loop.run_until_complete(self.async_get_colors())

    async def async_get_colors(self):
        colors = []
        resp, data = await self._async_get_brightness()
        if resp != Response.Success:
            return None
        for color in data:
            colors.append(color)
        return colors

    def get_colors_brightness(self):
        return self._loop.run_until_complete(self.async_get_colors_brightness())

    async def async_get_colors_brightness(self):
        colors = {}
        resp_b, brightness = await self._async_get_brightness()
        if resp_b != Response.Success:
            return None
        for color, value in brightness.items():
            colors[color] = self._primary_device.convert_to_percentage(color, value)
        return colors

    def set_colors_brightness(self, colors):
        return self._loop.run_until_complete(self.async_set_colors_brightness(colors))

    async def async_set_colors_brightness(self, colors):
        self._validate_connection()
        if self._primary_device is None or not isinstance(colors, dict):
            return Response.InvalidData
        expected = self._primary_device._mw_norm.keys()
        if colors.keys() - expected:
            return Response.NoSuchColour
        if colors.keys() != expected:
            return Response.AllColorsMustBeSpecified
        try:
            intensities = {
                color: self._primary_device.convert_to_intensity(color, value)
                for color, value in colors.items()
            }
        except (TypeError, ValueError):
            return Response.InvalidBrightnessValue

        for device in [self._primary_device, *self._other_devices]:
            milliwatts = sum(
                device.convert_to_mw(color, value)
                for color, value in intensities.items()
            )
            if milliwatts > device.max_mw:
                return Response.PowerLimitExceeded
        return await self._async_set_brightness(intensities)

    def patch_colors_brightness(self, colors):
        return self._loop.run_until_complete(self.async_patch_colors_brightness(colors))

    async def async_patch_colors_brightness(self, colors):
        if len(colors) < 1:
            return Response.InvalidData

        brightness = await self.async_get_colors_brightness()
        if brightness is None:
            return Response.Error

        for color, value in colors.items():
            brightness[color] = value

        return await self.async_set_colors_brightness(brightness)

    def update_color_brightness(self, color, value):
        return self._loop.run_until_complete(
            self.async_update_color_brightness(color, value)
        )

    async def async_update_color_brightness(self, color, value):
        if not color:
            return Response.InvalidData
        if value == 0:
            return Response.Success

        brightness = await self.async_get_colors_brightness()
        if brightness is None:
            return Response.Error

        brightness[color] += value
        return await self.async_set_colors_brightness(brightness)

    def _calculate_power(self, intensities):
        """Estimate LED watts for all paired lights using their calibration."""
        total_mw = sum(
            device.convert_to_mw(color, intensity)
            for device in [self._primary_device, *self._other_devices]
            for color, intensity in intensities.items()
        )
        return round(total_mw / 1000.0, 2)

    async def async_get_current_power_draw(self):
        _, intensities = await self._async_get_brightness()
        return self._calculate_power(intensities)

    async def async_get_state(self):
        """Use the same brightness sample for percentages and estimated power."""
        _, intensities = await self._async_get_brightness()
        schedule = await self.async_get_schedule_state()
        return {
            "colors": {
                color: self._primary_device.convert_to_percentage(color, value)
                for color, value in intensities.items()
            },
            "schedule": schedule,
            "power": self._calculate_power(intensities),
            "device_count": 1 + len(self._other_devices),
        }
