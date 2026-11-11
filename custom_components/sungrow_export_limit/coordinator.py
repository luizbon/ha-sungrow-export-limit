import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (CONF_CURTAIL_KW, CONF_FULL_KW, CONF_MIN_ELEVATION, CONF_MIN_INTERVAL, CONF_PRICE_ENTITY,
                    CONF_RESTORE_MINUTES, CONF_SUN_ENTITY, CONF_TRIGGER_PRICE, DEFAULTS, DOMAIN, SCAN_SECONDS)
from .policy import decide
from .sungrow_modbus import Dongle

_LOGGER = logging.getLogger(__name__)


class LimitCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_SECONDS))
        self.entry = entry
        self.dongle = Dongle(entry.data["host"])
        self.enabled = False  # the "Curtail on negative price" switch; off means hands off
        self._cancel_restore = None
        self._last_change = 0.0
        self._lock = asyncio.Lock()

    def opt(self, key):
        """Setting from options, else the original setup data, else the default."""
        return self.entry.options.get(key, self.entry.data.get(key, DEFAULTS.get(key)))

    @property
    def price_entity(self):
        return self.opt(CONF_PRICE_ENTITY)

    @property
    def sun_entity(self):
        return self.opt(CONF_SUN_ENTITY)

    @property
    def full_kw(self):
        """Limit when not curtailing: the configured value, never above the inverter's rated power."""
        rated = self.data["nominal_kw"]
        configured = self.opt(CONF_FULL_KW)
        return rated if not configured else min(float(configured), rated)

    @property
    def curtail_kw(self):
        return min(float(self.opt(CONF_CURTAIL_KW)), self.full_kw)

    def _float(self, entity, attr=None):
        st = self.hass.states.get(entity)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr) if attr else st.state)
        except (TypeError, ValueError):
            return None

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self.dongle.snapshot)
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"dongle unreachable: {exc}") from exc

    async def async_evaluate(self, *_):
        """Apply the rule. Called on price/sun changes, every poll, and when the switch flips."""
        if not self.data:
            return
        async with self._lock:
            target = decide(
                enabled=self.enabled, price=self._float(self.price_entity),
                trigger_price=float(self.opt(CONF_TRIGGER_PRICE)),
                sun_elevation=self._float(self.sun_entity, "elevation"),
                min_elevation=float(self.opt(CONF_MIN_ELEVATION)), limit_kw=self.data["limit_kw"],
                curtail_kw=self.curtail_kw, full_kw=self.full_kw,
                seconds_since_change=time.monotonic() - self._last_change,
                min_interval=float(self.opt(CONF_MIN_INTERVAL)))
            if target is None:
                if self.enabled and self.data["limit_kw"] < self.full_kw:
                    self._arm_restore()  # still curtailed on purpose: keep the lapse timer ahead of us
                return
            _LOGGER.info("export limit %s -> %s kW", self.data["limit_kw"], target)
            await self._write(target)

    async def async_set_limit(self, kw: float):
        """Manual set (number entity). Below the full limit it lapses back after the restore timeout."""
        async with self._lock:
            await self._write(kw)

    async def _write(self, kw):
        nominal = self.data["nominal_kw"]
        await self.hass.async_add_executor_job(self.dongle.write_limit, kw, nominal)
        self._last_change = time.monotonic()
        self._cancel_timer()
        if kw < self.full_kw:
            self._arm_restore()
        await self.async_request_refresh()

    def _cancel_timer(self):
        if self._cancel_restore:
            self._cancel_restore()
            self._cancel_restore = None

    def _arm_restore(self):
        self._cancel_timer()
        self._cancel_restore = async_call_later(self.hass, float(self.opt(CONF_RESTORE_MINUTES)) * 60, self._restore)

    async def _restore(self, _now=None):
        self._cancel_restore = None
        try:
            await self.hass.async_add_executor_job(self.dongle.write_limit, self.full_kw, self.data["nominal_kw"])
            _LOGGER.info("export limit restored to full")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("could not restore the export limit; retrying in 60 s")
            self._cancel_restore = async_call_later(self.hass, 60, self._restore)
            return
        await self.async_request_refresh()

    async def async_shutdown_restore(self):
        """Best effort on unload or HA stop: never leave the inverter curtailed."""
        self._cancel_timer()
        if self.data and self.data["limit_kw"] < self.full_kw:
            try:
                await self.hass.async_add_executor_job(self.dongle.write_limit, self.full_kw,
                                                       self.data["nominal_kw"])
            except Exception:  # noqa: BLE001
                _LOGGER.exception("could not restore the export limit on shutdown")
