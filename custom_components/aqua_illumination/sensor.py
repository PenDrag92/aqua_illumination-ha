"""Read-only channel, schedule, firmware and estimated power sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower

from .entity import AquaIlluminationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [
            AIPowerSensor(coordinator),
            AIDiagnosticSensor(coordinator, "Firmware Version", "firmware"),
            AIDiagnosticSensor(coordinator, "Connection Status", "connection"),
            AIScheduleSensor(coordinator),
            *(
                AIChannelBrightnessSensor(coordinator, channel)
                for channel in coordinator.data["colors"]
            ),
        ]
    )


class AIPowerSensor(AquaIlluminationEntity, SensorEntity):
    """Estimated LED power, not a mains measurement or energy counter."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "power_draw", "Estimated LED Power")

    @property
    def native_value(self):
        return self.coordinator.data["power"]

    @property
    def extra_state_attributes(self):
        return {"estimated": True, "includes_linked_lights": True}


class AIChannelBrightnessSensor(AquaIlluminationEntity, SensorEntity):
    """An un-clamped channel percentage, including HD overdrive."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-percent"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, channel):
        super().__init__(
            coordinator,
            f"{channel}_brightness_sensor",
            f"{channel.replace('_', ' ').title()} Brightness",
        )
        self._channel = channel

    @property
    def native_value(self):
        return round(self.coordinator.data["colors"][self._channel], 2)


class AIScheduleSensor(AquaIlluminationEntity, SensorEntity):
    """Read the schedule status without exposing a switch."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "schedule_state", "Schedule Status")

    @property
    def native_value(self):
        return "Scheduled" if self.coordinator.data["schedule"] else "Manual"


class AIDiagnosticSensor(AquaIlluminationEntity, SensorEntity):
    """Read-only diagnostics sharing the same availability handling."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, name, sensor_type):
        super().__init__(coordinator, sensor_type, name)
        self._type = sensor_type

    @property
    def native_value(self):
        if self._type == "firmware":
            return self.coordinator.api.firmware_version
        return "Connected"
