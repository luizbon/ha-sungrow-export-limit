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
    async_add_entities([*(Reading(c, entry.entry_id, *s) for s in SENSORS), TargetLimit(c, entry.entry_id)])


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


class TargetLimit(SungrowEntity, SensorEntity):
    """The limit the rule wants while limiting. In shadow mode this is only reported, never written."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "target_limit", "Target export limit")

    @property
    def native_value(self):
        c = self.coordinator
        held = c._claim or c._shadow
        return c.target_kw if held else None

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        return {"mode": "shadow" if c.shadow else "armed", "reason": c.reason,
                "house_load_floor_kw": c.floor_kw if c.data else None}
