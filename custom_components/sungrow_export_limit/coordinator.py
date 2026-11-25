import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (CONF_BATTERY_CHARGE_POSITIVE, CONF_BATTERY_CHARGE_W, CONF_BATTERY_POWER_ENTITY,
                    CONF_BLOCK_ENTITY, CONF_BLOCK_STATE, CONF_CUT_MARGIN_W, CONF_DEADBAND_W, CONF_ENTER_PRICE,
                    CONF_EXIT_HOLD_SECONDS, CONF_EXIT_PRICE, CONF_EXPORT_SEEN_W, CONF_FAST_EXIT_PRICE, CONF_GRID_ENTITY,
                    CONF_GRID_EXPORT_NEGATIVE, CONF_HOLD_SECONDS, CONF_IMPORT_SEEN_W, CONF_LOAD_ENTITY,
                    CONF_LOAD_ROUND_KW, CONF_LOCKOUT_MINUTES, CONF_MAX_HOURS, CONF_MIN_ELEVATION, CONF_MIN_EXPORT_W,
                    CONF_MIN_INTERVAL, CONF_MIN_LIMIT_KW, CONF_PORT, CONF_PRICE_ENTITY, CONF_RAISE_MARGIN_W,
                    CONF_RESTORE_MINUTES, CONF_RETRY_SECONDS, CONF_SCAN_SECONDS, CONF_SHADOW, CONF_SOC_ENTITY,
                    CONF_SOC_EXPORT_W, CONF_SOC_HIGH, CONF_STALE_SECONDS, CONF_STEP_KW, CONF_SUN_ENTITY, CONF_TIMEOUT,
                    DEFAULTS, DOMAIN, EVENT_EPISODE)
from .controller import TOLERANCE_KW, Reading, Settings, decide, floor_kw, start_condition
from .sungrow_modbus import Dongle

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


class LimitCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry):
        self.entry = entry  # opt() needs it before the base class is set up
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=float(self.opt(CONF_SCAN_SECONDS))))
        self.dongle = Dongle(entry.data["host"], int(self.opt(CONF_PORT)), float(self.opt(CONF_TIMEOUT)))
        self._subscribed = None  # what the listeners and the client were set up for
        self._cancel_restore = None
        self._last_change = -1e9
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # Set while this integration holds the limit down: the limit it found, the one it wrote, when it began.
        self._claim = None
        self._shadow = None  # the same, simulated, in shadow mode: {"original_kw", "limit_kw", "started"}
        self._exit_since = None  # monotonic time the price first sat at or above the stop price
        self._cleared = False  # the start condition went away since the lockout began
        self._lock_until = 0.0  # monotonic time the lockout after a maximum duration stop ends
        self._enter_since = None  # monotonic time the start condition began to hold
        self._locked_out = False  # set by the max duration stop until the start condition goes away
        self.target_kw = None  # last limit the rule wanted (written, or only reported in shadow mode)
        self.reason = "starting"

    def opt(self, key):
        """Setting from options, else the original setup data, else the default."""
        return self.entry.options.get(key, self.entry.data.get(key, DEFAULTS.get(key)))

    price_entity = property(lambda self: self.opt(CONF_PRICE_ENTITY))
    grid_entity = property(lambda self: self.opt(CONF_GRID_ENTITY))
    load_entity = property(lambda self: self.opt(CONF_LOAD_ENTITY))
    soc_entity = property(lambda self: self.opt(CONF_SOC_ENTITY))
    battery_power_entity = property(lambda self: self.opt(CONF_BATTERY_POWER_ENTITY))
    block_entity = property(lambda self: self.opt(CONF_BLOCK_ENTITY))
    sun_entity = property(lambda self: self.opt(CONF_SUN_ENTITY))

    @property
    def floor_kw(self):
        return floor_kw(self.settings, self._reading(self.data["limit_kw"]))

    @property
    def shadow(self):
        return bool(self.opt(CONF_SHADOW))

    @property
    def settings(self):
        o = self.opt
        return Settings(
            enter_price=float(o(CONF_ENTER_PRICE)), exit_price=float(o(CONF_EXIT_PRICE)),
            min_export_w=float(o(CONF_MIN_EXPORT_W)), soc_high=float(o(CONF_SOC_HIGH)),
            soc_export_w=float(o(CONF_SOC_EXPORT_W)), hold_seconds=float(o(CONF_HOLD_SECONDS)),
            min_limit_kw=float(o(CONF_MIN_LIMIT_KW)), deadband_kw=float(o(CONF_DEADBAND_W)) / 1000,
            max_seconds=float(o(CONF_MAX_HOURS)) * 3600, min_interval=float(o(CONF_MIN_INTERVAL)),
            exit_hold_seconds=float(o(CONF_EXIT_HOLD_SECONDS)),
            fast_exit_price=None if o(CONF_FAST_EXIT_PRICE) is None else float(o(CONF_FAST_EXIT_PRICE)),
            export_seen_w=float(o(CONF_EXPORT_SEEN_W)), import_seen_w=float(o(CONF_IMPORT_SEEN_W)),
            cut_margin_w=float(o(CONF_CUT_MARGIN_W)), raise_margin_w=float(o(CONF_RAISE_MARGIN_W)),
            step_kw=float(o(CONF_STEP_KW)), load_round_kw=float(o(CONF_LOAD_ROUND_KW)),
            battery_charge_w=float(o(CONF_BATTERY_CHARGE_W)))

    @property
    def watched_entities(self):
        return [e for e in (self.price_entity, self.grid_entity, self.load_entity, self.soc_entity,
                            self.battery_power_entity, self.block_entity, self.sun_entity) if e]

    @property
    def connection(self):
        """What the dongle client was built from: a change needs a reload."""
        return (self.entry.data["host"], int(self.opt(CONF_PORT)), float(self.opt(CONF_TIMEOUT)),
                float(self.opt(CONF_SCAN_SECONDS)))

    def _blocked(self):
        """The optional gate. Unknown or unavailable counts as blocked: do not start on a gate we cannot read."""
        if not self.block_entity:
            return False
        st = self.hass.states.get(self.block_entity)
        if st is None or st.state in ("unknown", "unavailable"):
            return True
        wanted = (self.opt(CONF_BLOCK_STATE) or "").strip().lower()
        return bool(wanted) and str(st.state).strip().lower() == wanted

    async def async_update_setting(self, key, value):
        """Persist a setting changed from an entity; the rule picks it up on the next evaluation."""
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, key: value})
        await self.async_evaluate()

    # --- sensors ---

    def _state(self, entity):
        """The state object if it holds a fresh number, else None. Stale readings are not trusted."""
        st = self.hass.states.get(entity) if entity else None
        if st is None:
            return None
        age = (dt_util.utcnow() - st.last_updated).total_seconds() if getattr(st, "last_updated", None) else 0
        return st if age <= float(self.opt(CONF_STALE_SECONDS)) else None

    def _float(self, entity, attr=None, fresh=True):
        st = self._state(entity) if fresh else self.hass.states.get(entity)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr) if attr else st.state)
        except (TypeError, ValueError):
            return None

    def _watts(self, entity):
        value = self._float(entity)
        if value is None:
            return None
        unit = (self.hass.states.get(entity).attributes.get("unit_of_measurement") or "").lower()
        return value * 1000 if unit == "kw" else value

    def _reading(self, limit_kw):
        """Everything the rule looks at. `healthy` is False when a sensor it needs is missing or stale."""
        price = self._float(self.price_entity)
        grid = self._watts(self.grid_entity)
        export = None if grid is None else (-grid if self.opt(CONF_GRID_EXPORT_NEGATIVE) else grid)
        load = self._watts(self.load_entity) if self.load_entity else None
        soc = self._float(self.soc_entity) if self.soc_entity else None
        battery = self._watts(self.battery_power_entity) if self.battery_power_entity else None
        if battery is not None and not self.opt(CONF_BATTERY_CHARGE_POSITIVE):
            battery = -battery
        elevation = self._float(self.sun_entity, "elevation")
        healthy = (price is not None and export is not None and bool(self.last_update_success)
                   and (not self.load_entity or load is not None) and (not self.soc_entity or soc is not None)
                   and (not self.battery_power_entity or battery is not None))
        return Reading(price=price, export_w=export, ac_w=float(self.data["ac_w"]), soc=soc, load_w=load,
                       battery_charge_w=battery, blocked=self._blocked(),
                       healthy=healthy, sun_ok=elevation is not None and elevation >= float(self.opt(CONF_MIN_ELEVATION)),
                       limit_kw=limit_kw)

    # --- the claim: what this integration changed, kept on disk so a restart can still undo it ---

    async def async_load_claim(self):
        saved = await self._store.async_load()
        if saved and saved.get("original_kw") is not None and saved.get("applied_kw") is not None:
            self._claim = {"original_kw": float(saved["original_kw"]), "applied_kw": float(saved["applied_kw"]),
                           "started": float(saved.get("started") or time.time())}
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
        self._fire("end", "limit_changed_by_someone_else")

    async def _read_limit(self):
        return await self.hass.async_add_executor_job(self.dongle.read_limit)

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self.dongle.snapshot)
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"dongle unreachable: {exc}") from exc

    def _fire(self, event, reason):
        reading = self._reading(self.data["limit_kw"]) if self.data else None
        started = (self._claim or self._shadow or {}).get("started")
        self.hass.bus.async_fire(EVENT_EPISODE, {
            "event": event, "mode": "shadow" if self.shadow else "armed", "reason": reason,
            "limit_kw": self.target_kw, "original_kw": (self._claim or self._shadow or {}).get("original_kw"),
            "duration_min": round((time.time() - started) / 60, 1) if started else 0,
            **({"price": reading.price, "export_w": reading.export_w, "load_w": reading.load_w,
                "soc": reading.soc, "ac_w": reading.ac_w} if reading else {})})

    # --- the rule ---

    async def async_evaluate(self, *_):
        """Apply the rule. Called on sensor changes, every poll, and when a setting changes."""
        if not self.data:
            return
        async with self._lock:
            try:
                await self._evaluate_locked()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("could not apply the export limit rule")

    async def _evaluate_locked(self):
        live = self.data["limit_kw"]
        if self._claim and abs(live - self._claim["applied_kw"]) >= TOLERANCE_KW:
            # Someone else (or a failed write of ours) changed the limit. It is not ours to undo.
            await self._external_change(live)
        if self.shadow and self._claim:
            await self._restore_locked("shadow_mode_on")  # shadow mode never keeps a real limit
            return
        if not self.shadow:
            self._shadow = None
        sim = self._shadow
        held = self._claim or sim
        r = self._reading(sim["limit_kw"] if sim else live)
        s = self.settings
        now = time.monotonic()
        if r.price is not None and r.export_w is not None:
            if start_condition(s, r):
                self._enter_since = self._enter_since or now
            else:
                self._enter_since = None
                self._cleared = True
        else:
            self._enter_since = None
        if self._locked_out and self._cleared and now >= self._lock_until:
            self._locked_out = False  # the conditions cleared once and the lockout time is over
        if r.price is not None and r.price >= s.exit_price:
            self._exit_since = self._exit_since or now
        else:
            self._exit_since = None
        d = decide(s, r, original_kw=held["original_kw"] if held else None,
                   enter_held_s=now - self._enter_since if self._enter_since else 0,
                   episode_s=time.time() - held["started"] if held else 0,
                   seconds_since_change=now - self._last_change, locked_out=self._locked_out,
                   exit_held_s=now - self._exit_since if self._exit_since else 0)
        self.reason = d.reason
        if d.action == "set":
            self.target_kw = d.target_kw
        elif d.action == "restore":
            self.target_kw = None
        if d.reason == "max_duration":
            self._locked_out, self._cleared = True, False
            self._lock_until = now + float(self.opt(CONF_LOCKOUT_MINUTES)) * 60
            _LOGGER.warning("limiting stopped after the maximum duration of %s h", self.opt(CONF_MAX_HOURS))
        if d.action == "restore":
            if sim:
                self._shadow = None
                self._fire("end", d.reason)
            else:
                await self._restore_locked(d.reason)
        elif d.action == "set":
            if self.shadow:
                await self._shadow_set(d, live)
            elif self._claim is None:
                await self._curtail(d.target_kw)
            else:
                await self._retarget(d.target_kw)
        elif d.action == "hold" and self._claim:
            self._arm_restore()  # still limiting on purpose: keep the lapse timer ahead of us

    async def _shadow_set(self, d, live):
        _LOGGER.info("shadow mode: would set the export limit %s -> %s kW (%s)", live, d.target_kw, d.reason)
        starting = self._shadow is None
        self._shadow = {**(self._shadow or {"original_kw": live, "started": time.time()}),
                        "limit_kw": d.target_kw}
        self._last_change = time.monotonic()
        self._fire("start" if starting else "update", d.reason)
        self.async_update_listeners()

    async def _curtail(self, target):
        """First step of an episode: read the limit now, keep it, then lower it. A failed read means no limit."""
        try:
            live = await self._read_limit()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("could not read the export limit, so it is not being lowered", exc_info=True)
            return
        if target >= live - TOLERANCE_KW:
            return  # already at or below the target
        await self._set_claim({"original_kw": live, "applied_kw": target, "started": time.time()})  # saved before the write
        _LOGGER.info("export limit %s -> %s kW (original kept)", live, target)
        await self._write(target)
        self._fire("start", "episode_start")

    async def _retarget(self, target):
        if not await self._still_ours():
            return
        _LOGGER.info("export limit %s -> %s kW", self._claim["applied_kw"], target)
        await self._write(target)  # only recorded once the write worked, so a failure is not mistaken for ours
        await self._set_claim({**self._claim, "applied_kw": target})
        self._fire("update", "following_house")

    async def _external_change(self, live):
        if abs(live - self._claim["original_kw"]) < TOLERANCE_KW:
            _LOGGER.info("the inverter limit is back at %s kW, so there is nothing to restore", live)
            self._cancel_timer()
            await self._set_claim(None)
        else:
            await self._release(f"the limit was changed to {live} kW by something else while curtailed "
                                f"(this integration set {self._claim['applied_kw']} kW, found "
                                f"{self._claim['original_kw']} kW)")

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

    async def _restore_locked(self, reason="restore"):
        """Put back the limit the inverter had before we lowered it, unless it was changed meanwhile."""
        if not await self._still_ours():
            return
        original = self._claim["original_kw"]
        await self._write_original(original, reason)

    async def _write_original(self, original, reason="restore"):
        nominal = max(self.data["nominal_kw"], original)
        await self.hass.async_add_executor_job(self.dongle.write_limit, original, nominal)
        _LOGGER.info("export limit restored to %s kW", original)
        self._last_change = time.monotonic()
        self._cancel_timer()
        self._fire("end", reason)
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
                _LOGGER.exception("could not restore the export limit; retrying later")
            if self._claim:  # not restored yet (read or write failed): try again
                self._cancel_restore = async_call_later(self.hass, float(self.opt(CONF_RETRY_SECONDS)), self._lapse)

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
