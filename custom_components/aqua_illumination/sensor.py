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
            AILastSuccessfulUpdateSensor(coordinator),
            AIDiagnosticSensor(coordinator, "Detected Lights", "detected_lights"),
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
        return {
            **super().extra_state_attributes,
            "estimated": True,
            "includes_linked_lights": True,
            "detected_lights": self.coordinator.data["device_count"],
            "child_lights": self.coordinator.data["device_count"] - 1,
        }


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
    """Connection status remains visible even during an outage."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, name, sensor_type):
        super().__init__(coordinator, sensor_type, name)
        self._type = sensor_type

    @property
    def native_value(self):
        if self._type == "firmware":
            return self.coordinator.api.firmware_version
        if self._type == "detected_lights":
            return self.coordinator.data["device_count"]
        return self.coordinator.connection_status

    @property
    def available(self):
        return self._type == "connection" or super().available

    @property
    def extra_state_attributes(self):
        if self._type == "connection":
            return self.coordinator.connection_diagnostics
        return super().extra_state_attributes


class AILastSuccessfulUpdateSensor(AquaIlluminationEntity, SensorEntity):
    """Age reference that cached reads never advance."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(
            coordinator, "last_successful_update", "Last Successful Update"
        )

    @property
    def available(self):
        return self.coordinator.last_successful_update is not None

    @property
    def native_value(self):
        return self.coordinator.last_successful_update
