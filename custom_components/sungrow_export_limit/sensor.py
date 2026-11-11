from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower

from .entity import SungrowEntity

SENSORS = (
    ("limit_kw", "Export limit", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    ("ac_w", "AC output power", UnitOfPower.WATT, SensorDeviceClass.POWER),
    ("ratio_pct", "Export limit ratio", PERCENTAGE, None),
)


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities(Reading(c, entry.entry_id, *s) for s in SENSORS)


class Reading(SungrowEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id, key, name, unit, device_class):
        super().__init__(coordinator, entry_id, key, name)
        self._key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    @property
    def native_value(self):
        return self.coordinator.data[self._key]
