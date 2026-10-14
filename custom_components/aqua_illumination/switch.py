"""Optional explicit control of the light's internal schedule."""

from homeassistant.components.switch import SwitchEntity

from .entity import AquaIlluminationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AIAutomatedScheduleSwitch(entry.runtime_data)])


class AIAutomatedScheduleSwitch(AquaIlluminationEntity, SwitchEntity):
    """The device's internal schedule, changed only by an explicit command."""

    _attr_icon = "mdi:calendar-clock"
    _requires_fresh_data = True

    def __init__(self, coordinator):
        super().__init__(coordinator, "schedule_switch", "Scheduled Mode")

    @property
    def is_on(self):
        return self.coordinator.data["schedule"]

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_schedule(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_schedule(False)
