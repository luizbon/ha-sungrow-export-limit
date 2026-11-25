from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_SHADOW
from .entity import SungrowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShadowMode(entry.runtime_data, entry.entry_id)])


class ShadowMode(SungrowEntity, SwitchEntity):
    """On: the rule runs and reports what it would do, but writes nothing. Off: it writes the limit."""

    _attr_icon = "mdi:ghost-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "shadow_mode", "Shadow mode")

    @property
    def is_on(self):
        return self.coordinator.shadow

    async def async_turn_on(self, **_kwargs):
        await self.coordinator.async_update_setting(CONF_SHADOW, True)

    async def async_turn_off(self, **_kwargs):
        await self.coordinator.async_update_setting(CONF_SHADOW, False)
