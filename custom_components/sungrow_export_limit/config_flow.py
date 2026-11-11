import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_MAX_EXPORT_KW, CONF_FULL_KW, CONF_MIN_ELEVATION, CONF_MIN_INTERVAL, CONF_PRICE_ENTITY,
                    CONF_RESTORE_MINUTES, CONF_SUN_ENTITY, CONF_TRIGGER_PRICE, DEFAULTS, DOMAIN)
from .sungrow_modbus import Dongle

PRICE = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
SUN = selector.EntitySelector(selector.EntitySelectorConfig(domain="sun"))


def _num(minimum, maximum, step="any", unit=None):
    config = {"min": minimum, "max": maximum, "step": step, "mode": selector.NumberSelectorMode.BOX}
    if unit:  # HA rejects unit_of_measurement=None
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


def _rule_schema(current: dict) -> vol.Schema:
    """Rule settings shared by setup and options. `current` pre-fills each field."""
    def d(key):
        return current.get(key, DEFAULTS.get(key))

    price_key = (vol.Required(CONF_PRICE_ENTITY, default=current[CONF_PRICE_ENTITY])
                 if current.get(CONF_PRICE_ENTITY) else vol.Required(CONF_PRICE_ENTITY))
    schema = {
        price_key: PRICE,
        vol.Required(CONF_TRIGGER_PRICE, default=d(CONF_TRIGGER_PRICE)): _num(-10, 10),
        vol.Required(CONF_MAX_EXPORT_KW, default=d(CONF_MAX_EXPORT_KW)): _num(0, 100, 0.01, "kW"),
        vol.Optional(CONF_FULL_KW, description={"suggested_value": current.get(CONF_FULL_KW)}): _num(
            0.01, 100, 0.01, "kW"),
        vol.Required(CONF_SUN_ENTITY, default=d(CONF_SUN_ENTITY)): SUN,
        vol.Required(CONF_MIN_ELEVATION, default=d(CONF_MIN_ELEVATION)): _num(0, 45, 1, "°"),
        vol.Required(CONF_RESTORE_MINUTES, default=d(CONF_RESTORE_MINUTES)): _num(1, 240, 1, "min"),
        vol.Required(CONF_MIN_INTERVAL, default=d(CONF_MIN_INTERVAL)): _num(10, 3600, 10, "s"),
    }
    return vol.Schema(schema)


def _check_rule(user_input: dict) -> dict:
    full = user_input.get(CONF_FULL_KW)
    if full and user_input[CONF_MAX_EXPORT_KW] >= full:
        return {"base": "max_export_not_below_full"}
    return {}


async def _probe(hass, host) -> dict:
    """Read the inverter through the dongle; returns a form error dict (empty when it works)."""
    try:
        await hass.async_add_executor_job(Dongle(host).snapshot)
    except Exception:  # noqa: BLE001
        return {"base": "cannot_connect"}
    return {}


class SungrowConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry):
        return SungrowOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = _check_rule(user_input) or await _probe(self.hass, user_input[CONF_HOST])
            if not errors:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Sungrow inverter", data=user_input)
        schema = vol.Schema({vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str}
                            ).extend(_rule_schema(user_input or {}).schema)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Change the dongle address (for example after a new DHCP lease)."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            errors = await _probe(self.hass, user_input[CONF_HOST])
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input,
                                                          unique_id=user_input[CONF_HOST])
        return self.async_show_form(
            step_id="reconfigure", errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}))


class SungrowOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry
        current = {**entry.data, **entry.options}
        if user_input is not None:
            errors = _check_rule(user_input)
            host = user_input[CONF_HOST]
            if not errors and host != entry.data[CONF_HOST]:
                errors = await _probe(self.hass, host)
            if not errors:
                if host != entry.data[CONF_HOST]:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_HOST: host}, unique_id=host)
                return self.async_create_entry(data={k: v for k, v in user_input.items() if k != CONF_HOST})
            current = {**current, **user_input}
        schema = vol.Schema({vol.Required(CONF_HOST, default=current[CONF_HOST]): str}
                            ).extend(_rule_schema(current).schema)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
