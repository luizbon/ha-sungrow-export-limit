"""The closed-loop rule, free of Home Assistant so it can be tested on its own.

Limiting starts only when the price is at or below the enter price AND the site is really exporting (or the
battery is full and exporting a little), both held for the hold time. While limiting, the limit follows the
house: it is cut by the export seen, raised when the site imports, and never goes below the house load or the
configured minimum, nor above the limit the inverter had before. Anything doubtful ends the episode.
"""
import math
from dataclasses import dataclass

TOLERANCE_KW = 0.005  # half the inverter register's 0.01 kW resolution: not a setting


@dataclass(frozen=True)
class Settings:
    enter_price: float
    exit_price: float
    min_export_w: float
    soc_high: float
    soc_export_w: float
    hold_seconds: float
    min_limit_kw: float
    deadband_kw: float
    max_seconds: float
    min_interval: float
    exit_hold_seconds: float = 0.0
    fast_exit_price: float | None = None
    export_seen_w: float = 40.0
    import_seen_w: float = 250.0
    cut_margin_w: float = 50.0
    raise_margin_w: float = 200.0
    step_kw: float = 0.05
    load_round_kw: float = 0.1
    battery_charge_w: float = 150.0


@dataclass(frozen=True)
class Reading:
    """What the site looks like now. None means unknown. `export_w` is negative when importing."""
    price: float | None
    export_w: float | None
    ac_w: float | None
    soc: float | None = None  # None when no battery sensor is configured
    load_w: float | None = None  # None when no load sensor is configured
    battery_charge_w: float | None = None  # None when no battery power sensor is configured; positive = charging
    blocked: bool = False  # the optional gate entity says not to limit
    healthy: bool = True  # every sensor that is needed is present and fresh
    sun_ok: bool = True
    limit_kw: float = 0.0  # the limit the inverter has (or, in shadow mode, would have)


@dataclass(frozen=True)
class Decision:
    action: str  # "idle" nothing to do, "hold" keep the limit, "set" write `target_kw`, "restore" put the original back
    target_kw: float | None = None
    reason: str = ""


def start_condition(s: Settings, r: Reading) -> bool:
    if r.price is None or r.export_w is None or r.price > s.enter_price:
        return False
    if r.export_w >= s.min_export_w:
        return True
    return r.soc is not None and r.soc >= s.soc_high and r.export_w >= s.soc_export_w


def floor_kw(s: Settings, r: Reading) -> float:
    """Never go below the configured minimum, nor below what the house is using."""
    step = s.load_round_kw
    load = round(math.ceil(r.load_w / 1000 / step - 1e-9) * step, 4) if r.load_w else 0.0
    return max(s.min_limit_kw, load)


def target_kw(s: Settings, r: Reading, original_kw: float) -> float:
    if r.export_w > s.export_seen_w:
        raw = r.ac_w - r.export_w - s.cut_margin_w
    elif r.export_w < -s.import_seen_w:
        raw = r.limit_kw * 1000 - r.export_w + s.raise_margin_w
    elif r.battery_charge_w is not None and r.battery_charge_w > s.battery_charge_w:
        raw = r.limit_kw * 1000 + r.battery_charge_w + s.raise_margin_w  # the battery absorbs it, no import needed
    else:
        raw = r.limit_kw * 1000
    stepped = round(raw / 1000 / s.step_kw) * s.step_kw
    return min(original_kw, max(floor_kw(s, r), round(stepped, 4)))


def decide(s: Settings, r: Reading, *, original_kw: float | None, enter_held_s: float, episode_s: float,
           seconds_since_change: float, locked_out: bool = False, exit_held_s: float = 0.0) -> Decision:
    """`original_kw` is the limit before we first lowered it, or None while we are not limiting."""
    if original_kw is not None:
        if not r.healthy:
            return Decision("restore", reason="sensors_unhealthy")
        if not r.sun_ok:
            return Decision("restore", reason="sun_low")
        if episode_s >= s.max_seconds:
            return Decision("restore", reason="max_duration")
        if r.blocked:
            return Decision("restore", reason="blocked")
        if r.price is None:
            return Decision("restore", reason="price_recovered")
        if s.fast_exit_price is not None and r.price >= s.fast_exit_price:
            return Decision("restore", reason="price_recovered_fast")
        if r.price >= s.exit_price and exit_held_s >= s.exit_hold_seconds:
            return Decision("restore", reason="price_recovered")
        target = target_kw(s, r, original_kw)
        if target >= original_kw - TOLERANCE_KW:
            return Decision("restore", reason="no_need_to_limit")
        if abs(target - r.limit_kw) < s.deadband_kw:
            return Decision("hold", reason="within_deadband")
        if target < r.limit_kw and seconds_since_change < s.min_interval:
            return Decision("hold", reason="flap_guard")  # lowering waits; raising never does
        return Decision("set", target, "following_house")
    if locked_out:
        return Decision("idle", reason="locked_out_after_max_duration")
    if not r.healthy:
        return Decision("idle", reason="sensors_unhealthy")
    if not r.sun_ok:
        return Decision("idle", reason="sun_low")
    if r.blocked:
        return Decision("idle", reason="blocked")
    if not start_condition(s, r):
        return Decision("idle", reason="conditions_not_met")
    if enter_held_s < s.hold_seconds:
        return Decision("idle", reason="waiting_to_confirm")
    target = target_kw(s, r, r.limit_kw)
    if target >= r.limit_kw - TOLERANCE_KW:
        return Decision("idle", reason="already_at_or_below_target")  # never raises, never writes
    if seconds_since_change < s.min_interval:
        return Decision("idle", reason="flap_guard")
    return Decision("set", target, "episode_start")
