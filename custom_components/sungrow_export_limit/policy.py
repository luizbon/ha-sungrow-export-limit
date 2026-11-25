"""The rule: price below the trigger price lowers the limit; the integration only ever undoes its own change."""

TOLERANCE_KW = 0.005


def decide(*, price, trigger_price, sun_elevation, min_elevation, limit_kw, triggered_kw, original_kw,
           seconds_since_change, min_interval):
    """Return the limit (kW) to write, or None to leave things alone.

    `original_kw` is the limit the inverter had before this integration first curtailed it, or None
    while the integration has not curtailed anything. Nothing is ever written while it is None unless
    the rule is triggered and the target is below the live limit: a limit that was already low (an
    installer or grid operator cap) is never raised. Anything unconfirmed (price or sun unknown, light
    ending) counts as not triggered, which restores `original_kw` when we are curtailing.
    """
    light = sun_elevation is not None and sun_elevation >= min_elevation
    triggered = price is not None and price < trigger_price and light
    if original_kw is None:
        if not triggered or triggered_kw >= limit_kw - TOLERANCE_KW:
            return None  # not curtailing, or nothing to cut: write nothing
        return None if seconds_since_change < min_interval else triggered_kw
    if not triggered:
        return original_kw  # restoring never waits
    target = min(triggered_kw, original_kw)  # never raise the limit above what it was
    if abs(target - limit_kw) < TOLERANCE_KW:
        return None
    if target < limit_kw and seconds_since_change < min_interval:
        return None  # lowering waits out the flap guard
    return target
