from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SungrowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CurtailSwitch(entry.runtime_data, entry.entry_id)])


class CurtailSwitch(SungrowEntity, SwitchEntity, RestoreEntity):
    """On: price below the trigger gives the curtailed limit, anything else the full limit. Off: hands off."""

    _attr_icon = "mdi:solar-power-variant-outline"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "curtail_on_negative_price", "Curtail on negative price")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.enabled = bool(last and last.state == "on")  # starts off on first install

    @property
    def is_on(self):
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs):
        self.coordinator.enabled = True
        self.async_write_ha_state()
        await self.coordinator.async_evaluate()

    async def async_turn_off(self, **kwargs):
        self.coordinator.enabled = False
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
