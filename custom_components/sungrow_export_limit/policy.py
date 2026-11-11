"""The rule: price below the trigger gives the curtailed limit, otherwise the full limit."""


def decide(*, enabled, price, trigger_price, sun_elevation, min_elevation, limit_kw, curtail_kw, full_kw,
           seconds_since_change, min_interval):
    """Return the limit (kW) to write, or None to leave things alone.

    Disabled means hands off. Enabled with anything unconfirmed (price or sun unknown, light ending)
    means full export.
    """
    if not enabled:
        return None
    light = sun_elevation is not None and sun_elevation >= min_elevation
    triggered = price is not None and price < trigger_price and light
    target = curtail_kw if triggered else full_kw
    if abs(target - limit_kw) < 0.005:
        return None
    if target < limit_kw and seconds_since_change < min_interval:
        return None  # lowering waits out the flap guard; raising never does
    return target
