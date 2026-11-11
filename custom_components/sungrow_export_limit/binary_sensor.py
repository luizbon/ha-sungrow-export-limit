from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import SungrowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Curtailed(entry.runtime_data, entry.entry_id)])


class Curtailed(SungrowEntity, BinarySensorEntity):
    """On while the live limit is below the full limit (the trigger is holding export down)."""

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "curtailed", "Export curtailed")

    @property
    def is_on(self):
        return self.coordinator.data["limit_kw"] < self.coordinator.full_kw - 0.005
