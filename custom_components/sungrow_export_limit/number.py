from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfPower
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_MAX_EXPORT_KW, CONF_TRIGGER_PRICE
from .entity import SungrowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([TriggerPrice(c, entry.entry_id), MaxExport(c, entry.entry_id)])


class TriggerPrice(SungrowEntity, NumberEntity):
    """The price that fires the rule: export is limited while the price sensor reads below this."""

    _attr_native_min_value = -10
    _attr_native_max_value = 10
    _attr_native_step = 0.001
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:cash-lock-open"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "trigger_price", "Trigger price")

    @property
    def native_unit_of_measurement(self):
        st = self.hass.states.get(self.coordinator.price_entity) if self.hass else None
        return st.attributes.get("unit_of_measurement") if st else None

    @property
    def native_value(self):
        return self.coordinator.trigger_price

    async def async_set_native_value(self, value):
        await self.coordinator.async_update_setting(CONF_TRIGGER_PRICE, value)


class MaxExport(SungrowEntity, NumberEntity):
    """How much the inverter may export while the trigger is hit. 0 stops export."""

    _attr_native_min_value = 0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "max_export_when_triggered", "Maximum export when triggered")

    @property
    def native_max_value(self):
        return self.coordinator.data["nominal_kw"]

    @property
    def native_value(self):
        return self.coordinator.max_export_kw

    async def async_set_native_value(self, value):
        await self.coordinator.async_update_setting(CONF_MAX_EXPORT_KW, value)
