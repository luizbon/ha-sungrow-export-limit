import importlib.util, os, unittest

spec = importlib.util.spec_from_file_location(
    "controller", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components",
                               "sungrow_export_limit", "controller.py"))
c = importlib.util.module_from_spec(spec)
import sys; sys.modules["controller"] = c  # dataclasses needs the module registered
spec.loader.exec_module(c)

S = c.Settings(enter_price=-0.005, exit_price=0.005, min_export_w=150, soc_high=93, soc_export_w=50,
               hold_seconds=120, min_limit_kw=0.3, deadband_kw=0.25, max_seconds=4 * 3600, min_interval=90)
R = c.Reading(price=-0.01, export_w=2000, ac_w=3000, limit_kw=5.0)


def idle(r=R, **kw):
    return c.decide(S, r, **{**dict(original_kw=None, enter_held_s=999, episode_s=0, seconds_since_change=999), **kw})


def held(r=None, **kw):
    r = r or R
    return c.decide(S, r, **{**dict(original_kw=5.0, enter_held_s=999, episode_s=60, seconds_since_change=999), **kw})


def rd(**kw):
    return c.Reading(**{**R.__dict__, **kw})


class Starting(unittest.TestCase):
    def test_starts_and_cuts_by_the_export_seen(self):
        d = idle()
        self.assertEqual((d.action, d.target_kw), ("set", 0.95))  # 3000 - 2000 - 50 W

    def test_needs_both_price_and_export(self):
        self.assertEqual(idle(rd(price=0.1)).action, "idle")
        self.assertEqual(idle(rd(export_w=100)).action, "idle")
        self.assertEqual(idle(rd(export_w=None)).action, "idle")
        self.assertEqual(idle(rd(price=None)).action, "idle")

    def test_price_at_the_threshold_counts(self):
        self.assertEqual(idle(rd(price=-0.005)).action, "set")
        self.assertEqual(idle(rd(price=-0.004)).action, "idle")

    def test_full_battery_starts_on_a_smaller_export(self):
        self.assertEqual(idle(rd(export_w=60, soc=95)).action, "set")
        self.assertEqual(idle(rd(export_w=60, soc=80)).action, "idle")
        self.assertEqual(idle(rd(export_w=40, soc=95)).action, "idle")

    def test_waits_for_the_hold_time(self):
        self.assertEqual(idle(enter_held_s=119).reason, "waiting_to_confirm")
        self.assertEqual(idle(enter_held_s=120).action, "set")

    def test_unhealthy_or_dark_never_starts(self):
        self.assertEqual(idle(rd(healthy=False)).action, "idle")
        self.assertEqual(idle(rd(sun_ok=False)).action, "idle")

    def test_never_raises_and_never_writes_when_already_lower(self):
        self.assertEqual(idle(rd(limit_kw=0.5)).action, "idle")  # target 0.95 is above the 0.5 cap
        self.assertEqual(idle(rd(limit_kw=0.95)).action, "idle")

    def test_flap_guard_delays_starting(self):
        self.assertEqual(idle(seconds_since_change=10).action, "idle")

    def test_locked_out_after_max_duration(self):
        self.assertEqual(idle(locked_out=True).action, "idle")


class Floor(unittest.TestCase):
    def test_minimum_limit(self):
        self.assertEqual(idle(rd(ac_w=2100)).target_kw, 0.3)  # 2100-2000-50 = 50 W, below the minimum

    def test_house_load_floor(self):
        self.assertEqual(idle(rd(load_w=1230)).target_kw, 1.3)  # 1.23 kW rounded up to 0.1

    def test_never_above_the_original(self):
        self.assertEqual(idle(rd(load_w=9000, limit_kw=5.0)).action, "idle")


class Limiting(unittest.TestCase):
    def test_follows_the_house_down_and_up(self):
        r = rd(limit_kw=0.95, ac_w=950, export_w=600)  # still exporting 600 W
        self.assertEqual(held(r).target_kw, 0.3)
        r = rd(limit_kw=0.95, ac_w=950, export_w=-500)  # importing: we cut too much
        d = held(r)
        self.assertEqual((d.action, d.target_kw), ("set", 1.65))

    def test_deadband_holds(self):
        self.assertEqual(held(rd(limit_kw=0.95, ac_w=950, export_w=0)).action, "hold")

    def test_lowering_waits_but_raising_does_not(self):
        r = rd(limit_kw=2.0, ac_w=2000, export_w=1000)
        self.assertEqual(held(r, seconds_since_change=10).reason, "flap_guard")
        r = rd(limit_kw=0.95, ac_w=950, export_w=-500)
        self.assertEqual(held(r, seconds_since_change=10).action, "set")

    def test_every_doubt_restores(self):
        for kw, reason in ((dict(price=0.01), "price_recovered"), (dict(price=None), "price_recovered"),
                           (dict(healthy=False), "sensors_unhealthy"), (dict(sun_ok=False), "sun_low")):
            d = held(rd(limit_kw=0.95, **kw))
            self.assertEqual((d.action, d.reason), ("restore", reason))

    def test_max_duration_restores(self):
        d = held(rd(limit_kw=0.95), episode_s=4 * 3600)
        self.assertEqual((d.action, d.reason), ("restore", "max_duration"))

    def test_price_hysteresis_holds_between_enter_and_exit(self):
        self.assertNotEqual(held(rd(limit_kw=0.95, price=0.0)).action, "restore")

    def test_no_need_to_limit_restores(self):
        d = held(rd(limit_kw=0.95, ac_w=900, export_w=0, load_w=9000))  # house needs more than the old limit
        self.assertEqual((d.action, d.reason), ("restore", "no_need_to_limit"))


if __name__ == "__main__":
    unittest.main()
