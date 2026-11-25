import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (CONF_MAX_EXPORT_KW, CONF_MIN_ELEVATION, CONF_MIN_INTERVAL, CONF_PRICE_ENTITY,
                    CONF_RESTORE_MINUTES, CONF_SUN_ENTITY, CONF_TRIGGER_PRICE, DEFAULTS, DOMAIN, SCAN_SECONDS)
from .policy import TOLERANCE_KW, decide
from .sungrow_modbus import Dongle

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


class LimitCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_SECONDS))
        self.entry = entry
        self.dongle = Dongle(entry.data["host"])
        self._subscribed = None  # (price entity, sun entity) the listeners were set up for
        self._cancel_restore = None
        self._last_change = 0.0
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._claim = None  # {"original_kw", "applied_kw"} while this integration holds the limit down

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
    def max_export_kw(self):
        return min(float(self.opt(CONF_MAX_EXPORT_KW)), self.data["nominal_kw"])

    @property
    def trigger_price(self):
        return float(self.opt(CONF_TRIGGER_PRICE))

    async def async_update_setting(self, key, value):
        """Persist a setting changed from an entity; the rule picks it up on the next evaluation."""
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, key: value})
        await self.async_evaluate()

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

    # --- the claim: what this integration changed, kept on disk so a restart can still undo it ---

    async def async_load_claim(self):
        saved = await self._store.async_load()
        if saved and saved.get("original_kw") is not None and saved.get("applied_kw") is not None:
            self._claim = {"original_kw": float(saved["original_kw"]), "applied_kw": float(saved["applied_kw"])}
            _LOGGER.info("resuming: the inverter limit was %s kW before this integration lowered it to %s kW",
                         self._claim["original_kw"], self._claim["applied_kw"])

    @staticmethod
    async def async_remove_claim(hass, entry):
        await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()

    @property
    def curtailed(self):
        return self._claim is not None

    @property
    def original_kw(self):
        return self._claim["original_kw"] if self._claim else None

    async def _set_claim(self, claim):
        self._claim = claim
        if claim is None:
            await self._store.async_remove()
        else:
            await self._store.async_save(claim)

    async def _release(self, why):
        """Give up the claim without writing: the inverter is no longer ours to restore."""
        _LOGGER.warning("not restoring the export limit: %s", why)
        self._cancel_timer()
        await self._set_claim(None)

    async def _read_limit(self):
        return await self.hass.async_add_executor_job(self.dongle.read_limit)

    async def async_evaluate(self, *_):
        """Apply the rule. Called on price/sun changes, every poll, and when a setting changes."""
        if not self.data:
            return
        async with self._lock:
            try:
                await self._evaluate_locked()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("could not apply the export limit rule")

    async def _evaluate_locked(self):
        if self._claim and abs(self.data["limit_kw"] - self._claim["applied_kw"]) >= TOLERANCE_KW:
            # Someone else (or a failed write of ours) changed the limit. It is not ours to undo.
            await self._external_change(self.data["limit_kw"])
        target = decide(
            price=self._float(self.price_entity), trigger_price=self.trigger_price,
            sun_elevation=self._float(self.sun_entity, "elevation"),
            min_elevation=float(self.opt(CONF_MIN_ELEVATION)), limit_kw=self.data["limit_kw"],
            triggered_kw=self.max_export_kw, original_kw=self.original_kw,
            seconds_since_change=time.monotonic() - self._last_change,
            min_interval=float(self.opt(CONF_MIN_INTERVAL)))
        if target is None:
            if self._claim:
                self._arm_restore()  # still curtailed on purpose: keep the lapse timer ahead of us
            return
        if self._claim is None:
            await self._curtail(target)
        elif target >= self._claim["original_kw"] - TOLERANCE_KW:
            await self._restore_locked()
        else:
            await self._lower_further(target)

    async def _external_change(self, live):
        if abs(live - self._claim["original_kw"]) < TOLERANCE_KW:
            _LOGGER.info("the inverter limit is back at %s kW, so there is nothing to restore", live)
            self._cancel_timer()
            await self._set_claim(None)
        else:
            await self._release(f"the limit was changed to {live} kW by something else while curtailed "
                                f"(this integration set {self._claim['applied_kw']} kW, found "
                                f"{self._claim['original_kw']} kW)")

    async def _curtail(self, target):
        """First curtail: read the limit now, keep it, then lower it. A failed read means no curtail."""
        try:
            live = await self._read_limit()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("could not read the export limit, so it is not being lowered", exc_info=True)
            return
        if target >= live - TOLERANCE_KW:
            return  # already at or below the target
        await self._set_claim({"original_kw": live, "applied_kw": target})  # saved before the write
        _LOGGER.info("export limit %s -> %s kW (original kept)", live, target)
        await self._write(target)

    async def _lower_further(self, target):
        if not await self._still_ours():
            return
        _LOGGER.info("export limit %s -> %s kW", self._claim["applied_kw"], target)
        await self._write(target)  # only recorded once the write worked, so a failure is not mistaken for ours
        await self._set_claim({**self._claim, "applied_kw": target})

    async def _still_ours(self):
        """Fresh read before touching a limit we hold: if it changed, leave it alone. False = do nothing."""
        try:
            live = await self._read_limit()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("could not read the export limit; leaving it and trying again later", exc_info=True)
            return False
        if abs(live - self._claim["applied_kw"]) >= TOLERANCE_KW:
            await self._external_change(live)
            return False
        return True

    async def _write(self, kw):
        claim = self._claim
        nominal = max(self.data["nominal_kw"], claim["original_kw"] if claim else 0)
        await self.hass.async_add_executor_job(self.dongle.write_limit, kw, nominal)
        self._last_change = time.monotonic()
        self._cancel_timer()
        self.async_set_updated_data({**self.data, "limit_kw": kw})
        if claim:
            self._arm_restore()
        await self.async_request_refresh()

    async def _restore_locked(self):
        """Put back the limit the inverter had before we lowered it, unless it was changed meanwhile."""
        if not await self._still_ours():
            return
        original = self._claim["original_kw"]
        await self._write_original(original)

    async def _write_original(self, original):
        nominal = max(self.data["nominal_kw"], original)
        await self.hass.async_add_executor_job(self.dongle.write_limit, original, nominal)
        _LOGGER.info("export limit restored to %s kW", original)
        self._last_change = time.monotonic()
        self._cancel_timer()
        await self._set_claim(None)
        self.async_set_updated_data({**self.data, "limit_kw": original})
        await self.async_request_refresh()

    def _cancel_timer(self):
        if self._cancel_restore:
            self._cancel_restore()
            self._cancel_restore = None

    def _arm_restore(self):
        self._cancel_timer()
        self._cancel_restore = async_call_later(self.hass, float(self.opt(CONF_RESTORE_MINUTES)) * 60, self._lapse)

    async def _lapse(self, _now=None):
        """The restore time ran out without the rule renewing the limit: put the original back."""
        self._cancel_restore = None
        async with self._lock:
            if not self._claim:
                return
            try:
                await self._restore_locked()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("could not restore the export limit; retrying in 60 s")
            if self._claim:  # not restored yet (read or write failed): try again
                self._cancel_restore = async_call_later(self.hass, 60, self._lapse)

    async def async_shutdown_restore(self):
        """Best effort on unload or HA stop: put back the limit we lowered. The claim stays saved if it fails."""
        self._cancel_timer()
        if not self._claim:
            return
        async with self._lock:
            if not self._claim:
                return
            try:
                if await self._still_ours():
                    await self._write_original(self._claim["original_kw"])
            except Exception:  # noqa: BLE001
                _LOGGER.exception("could not restore the export limit on shutdown")
