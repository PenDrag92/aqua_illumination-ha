"""Read-only requests; these buttons never change the lamp's schedule."""

from homeassistant.components.button import ButtonEntity

from .entity import AquaIlluminationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AIRefreshScheduleButton(entry.runtime_data)])


class AIRefreshScheduleButton(AquaIlluminationEntity, ButtonEntity):
    """Allow rereading a plan after it was edited in the manufacturer's app."""

    _attr_icon = "mdi:calendar-refresh"
    _requires_fresh_data = True

    def __init__(self, coordinator):
        super().__init__(coordinator, "refresh_schedule", "Refresh Schedule")

    async def async_press(self):
        await self.coordinator.async_refresh_schedule()
