from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .coordinator import LimitCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = LimitCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _evaluate(*_args) -> None:
        # Plain lambdas run in a worker thread; a @callback runs in the event loop.
        entry.async_create_background_task(hass, coordinator.async_evaluate(), "sungrow rule")

    @callback
    def _restore_on_stop(_event) -> None:
        hass.async_create_task(coordinator.async_shutdown_restore())  # tracked, so HA waits for it

    entry.async_on_unload(async_track_state_change_event(
        hass, [coordinator.price_entity, coordinator.sun_entity], _evaluate))
    entry.async_on_unload(coordinator.async_add_listener(_evaluate))
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _restore_on_stop))
    coordinator._subscribed = (coordinator.price_entity, coordinator.sun_entity)

    async def _changed(hass: HomeAssistant, changed: ConfigEntry) -> None:
        # Different sensors need new state listeners, so reload; plain value changes just re-run the rule.
        if (coordinator.price_entity, coordinator.sun_entity) != coordinator._subscribed:
            hass.config_entries.async_schedule_reload(changed.entry_id)
        else:
            await coordinator.async_evaluate()

    entry.async_on_unload(entry.add_update_listener(_changed))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await entry.runtime_data.async_shutdown_restore()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
