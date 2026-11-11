from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfPower

from .entity import SungrowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ExportLimit(entry.runtime_data, entry.entry_id)])


class ExportLimit(SungrowEntity, NumberEntity):
    """Feed-in limit. Setting it below the nominal power lapses back to full after 15 minutes."""

    _attr_native_min_value = 0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "export_limit", "Export limit")

    @property
    def native_max_value(self):
        return self.coordinator.data["nominal_kw"]

    @property
    def native_value(self):
        return self.coordinator.data["limit_kw"]

    @property
    def extra_state_attributes(self):
        return {"limit_switch_on": self.coordinator.data["switch_on"]}

    async def async_set_native_value(self, value):
        await self.coordinator.async_set_limit(value)
