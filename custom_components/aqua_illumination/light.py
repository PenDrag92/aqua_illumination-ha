"""Optional manual control of individual AI color channels."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity

from .entity import AquaIlluminationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        AquaIlluminationLight(coordinator, channel)
        for channel in coordinator.data["colors"]
    )


class AquaIlluminationLight(AquaIlluminationEntity, LightEntity):
    """A channel using HA's 0-255 brightness scale."""

    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, coordinator, channel):
        super().__init__(
            coordinator, f"{channel}_light", channel.replace("_", " ").title()
        )
        self._channel = channel

    @property
    def is_on(self):
        return self.coordinator.data["colors"][self._channel] > 0

    @property
    def brightness(self):
        # HA brightness must be an integer <=255. Sensors retain the full HD value.
        percentage = self.coordinator.data["colors"][self._channel]
        return round(min(100, max(0, percentage)) * 255 / 100)

    async def async_turn_on(self, **kwargs):
        target = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
        await self.coordinator.async_set_channel(self._channel, target * 100 / 255)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_channel(self._channel, 0)
