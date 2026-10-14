"""Shared device information and availability for AI entities."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class AquaIlluminationEntity(CoordinatorEntity):
    """An entity whose availability follows the shared coordinator."""

    _attr_has_entity_name = True
    _requires_fresh_data = False

    def __init__(self, coordinator, suffix, name):
        super().__init__(coordinator)
        api = coordinator.api
        self._attr_name = name
        self._attr_unique_id = f"{api.mac_addr}_{suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, api.mac_addr)},
            "name": api.name,
            "manufacturer": "Aqua Illumination",
            "sw_version": api.firmware_version,
        }

    @property
    def available(self):
        return super().available and not (
            self._requires_fresh_data and self.coordinator.data_stale
        )

    @property
    def extra_state_attributes(self):
        attributes = {"data_stale": self.coordinator.data_stale}
        if self.coordinator.data_stale:
            attributes["last_successful_update"] = (
                self.coordinator.last_successful_update.isoformat()
            )
        return attributes
