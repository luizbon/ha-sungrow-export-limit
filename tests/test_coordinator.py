"""The coordinator must only undo what it did itself. Runs the real coordinator against a fake dongle."""
import asyncio, os, sys, time, unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

try:
    import homeassistant  # noqa: F401
    HAVE_HA = True
except ImportError:
    HAVE_HA = False

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class FakeDongle:
    def __init__(self, limit):
        self.limit, self.writes, self.read_fails, self.write_fails = limit, [], False, False

    def read_limit(self):
        if self.read_fails:
            raise ConnectionError("down")
        return self.limit

    def write_limit(self, kw, max_kw):
        if self.write_fails:
            raise ConnectionError("down")
        assert 0 <= kw <= max_kw
        self.writes.append(kw)
        self.limit = kw


class FakeStore:
    saved = {}  # survives a "restart" because tests share it

    def __init__(self, *_a):
        pass

    async def async_load(self):
        return FakeStore.saved.get("v")

    async def async_save(self, data):
        FakeStore.saved["v"] = dict(data)

    async def async_remove(self):
        FakeStore.saved.pop("v", None)


@unittest.skipUnless(HAVE_HA, "homeassistant not installed")
class Claim(unittest.TestCase):
    def setUp(self):
        FakeStore.saved = {}
        from custom_components.sungrow_export_limit import coordinator as co
        self.co = co
        patches = [mock.patch.object(co, "Store", FakeStore),
                   mock.patch.object(co, "async_call_later", lambda *a, **k: (lambda: None))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.price, self.grid, self.ac = -0.01, -2000.0, 3000  # exporting 2 kW at a negative price

    def make(self, limit=5.0, **options):
        """A coordinator whose polled data is `limit`, as after a restart or a poll."""
        co = self.co
        c = object.__new__(co.LimitCoordinator)
        c.hass = SimpleNamespace(states=SimpleNamespace(get=lambda e: c._states.get(e)),
                                 async_add_executor_job=self._run,
                                 bus=SimpleNamespace(async_fire=lambda t, d: c.events.append(d)))
        c.entry = SimpleNamespace(entry_id="e1", options={}, data={
            "host": "h", "price_entity": "sensor.p", "grid_entity": "sensor.g", "shadow_mode": False,
            "hold_seconds": 0, "exit_hold_seconds": 0, "sun_entity": "sun.sun", **options})
        c.dongle = FakeDongle(limit)
        c.events = []
        c._states = {}
        self.sensors(c)
        c._cancel_restore, c._last_change, c._lock = None, -1e9, asyncio.Lock()
        c._store, c._claim, c._shadow, c._enter_since, c._locked_out = FakeStore(), None, None, None, False
        c._exit_since, c._lock_until, c._cleared = None, 0.0, False
        c.target_kw, c.reason, c.last_update_success = None, "", True
        c.data = {"nominal_kw": 5.0, "limit_kw": limit, "ac_w": self.ac}

        async def refresh():
            c.data = {**c.data, "limit_kw": c.dongle.limit}
        c.async_set_updated_data = lambda data: setattr(c, "data", data)
        c.async_update_listeners = lambda: None
        c.async_request_refresh = refresh
        return c

    def sensors(self, c, age=0, **extra):
        from homeassistant.util import dt as dt_util
        when = dt_util.utcnow() - timedelta(seconds=age)

        def st(state, **attrs):
            return SimpleNamespace(state=str(state), attributes=attrs, last_updated=when)
        c._states.update({"sensor.p": st(self.price), "sensor.g": st(self.grid, unit_of_measurement="W"),
                          "sun.sun": st("above", elevation=40), **extra})

    async def _run(self, fn, *args):
        return fn(*args)

    def go(self, coro):
        return asyncio.run(coro)

    def set_price(self, c, price):
        self.price = price
        self.sensors(c)

    # --- nothing is written unless we curtail ---

    def test_no_trigger_writes_nothing_even_with_a_lower_limit_on_the_inverter(self):
        self.price = 0.1
        for limit in (5.0, 2.0):
            c = self.make(limit)
            self.go(c.async_evaluate())
            self.assertEqual(c.dongle.writes, [])
            self.assertFalse(c.curtailed)

    def test_unavailable_or_stale_price_writes_nothing(self):
        c = self.make(2.0)
        c._states["sensor.p"] = SimpleNamespace(state="unavailable", attributes={}, last_updated=None)
        self.go(c.async_evaluate())
        self.sensors(c, age=3600)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_negative_price_without_export_writes_nothing(self):
        self.grid = 300.0  # importing
        c = self.make(5.0)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_does_not_raise_a_limit_already_below_the_target(self):
        c = self.make(0.5)  # target would be 0.95 kW
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])
        self.assertFalse(c.curtailed)

    def test_hold_time_must_pass_first(self):
        c = self.make(5.0, hold_seconds=120)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    # --- curtail, remember, restore ---

    def test_curtail_stores_what_it_read_and_restores_that(self):
        c = self.make(3.0)  # installer cap of 3 kW
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95])
        self.assertEqual(FakeStore.saved["v"]["original_kw"], 3.0)
        self.assertEqual(FakeStore.saved["v"]["applied_kw"], 0.95)
        self.assertTrue(c.curtailed)
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95, 3.0])  # 3 kW, not rated power (5 kW)
        self.assertFalse(c.curtailed)
        self.assertEqual(FakeStore.saved, {})
        self.assertEqual([e["event"] for e in c.events], ["start", "end"])

    def test_read_failure_at_curtail_time_does_not_curtail(self):
        c = self.make(5.0)
        c.dongle.read_fails = True
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])
        self.assertFalse(c.curtailed)
        self.assertEqual(FakeStore.saved, {})

    def test_failed_curtail_write_leaves_nothing_to_undo(self):
        c = self.make(5.0)
        c.dongle.write_fails = True
        self.go(c.async_evaluate())
        self.assertTrue(c.curtailed)  # saved first, in case the write landed
        c.dongle.write_fails = False
        self.go(c.async_evaluate())  # inverter still reads 5.0, so the claim is dropped quietly...
        self.assertEqual(c.dongle.limit, 0.95)  # ...and the rule curtails again from a fresh read
        self.assertEqual(FakeStore.saved["v"]["original_kw"], 5.0)

    def test_lapse_restores_the_stored_original(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        self.go(c._lapse())
        self.assertEqual(c.dongle.limit, 3.0)
        self.assertFalse(c.curtailed)

    def test_unload_restores_the_stored_original(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        self.go(c.async_shutdown_restore())
        self.assertEqual(c.dongle.limit, 3.0)

    def test_unload_when_never_curtailed_writes_nothing(self):
        c = self.make(2.0)
        self.go(c.async_shutdown_restore())
        self.assertEqual(c.dongle.writes, [])

    def test_failed_shutdown_restore_keeps_the_claim(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.dongle.write_fails = True
        self.go(c.async_shutdown_restore())
        self.assertEqual(FakeStore.saved["v"]["original_kw"], 3.0)

    # --- the closed loop ---

    def test_follows_the_house_up_when_importing(self):
        c = self.make(5.0)
        self.go(c.async_evaluate())
        self.grid, self.ac = 500.0, 950  # now importing 500 W
        c.data = {**c.data, "ac_w": 950}
        self.sensors(c)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95, 1.65])
        self.assertEqual(FakeStore.saved["v"]["original_kw"], 5.0)

    def test_house_load_sets_the_floor(self):
        c = self.make(5.0, load_entity="sensor.l")
        self.sensors(c, **{"sensor.l": SimpleNamespace(state="1.2", last_updated=c._states["sensor.p"].last_updated,
                                                       attributes={"unit_of_measurement": "kW"})})
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [1.2])  # 0.95 would starve the house

    def test_stale_sensor_during_an_episode_restores(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        self.sensors(c, age=3600)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.limit, 3.0)
        self.assertEqual(c.events[-1]["reason"], "sensors_unhealthy")

    def test_max_duration_restores_and_locks_out(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c._claim["started"] = time.time() - 5 * 3600
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.limit, 3.0)
        c._last_change = -1e9
        self.go(c.async_evaluate())  # conditions still hold, but it must not start again
        self.assertEqual(c.dongle.writes, [0.95, 3.0])
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())  # conditions clear...
        self.set_price(c, -0.01)
        self.go(c.async_evaluate())  # ...so it may start a new episode
        self.assertEqual(c.dongle.writes[-1], 0.95)

    # --- every tunable changes behaviour ---

    def test_cut_margin_and_step_options(self):
        c = self.make(5.0, cut_margin_w=0, step_kw=0.5)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [1.0])  # 3000 - 2000 - 0 W = 1.0 kW (default: 0.95)

    def test_min_limit_option(self):
        c = self.make(5.0, min_limit_kw=2.0)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [2.0])

    def test_enter_price_option(self):
        c = self.make(5.0, enter_price=-0.05)  # -0.01 is not low enough now
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_min_export_option(self):
        c = self.make(5.0, min_export_w=2500)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_soc_options(self):
        self.grid = -80.0
        c = self.make(5.0, soc_entity="sensor.s", soc_high=90, soc_export_w=50)
        self.sensors(c, **{"sensor.s": SimpleNamespace(state="95", attributes={}, last_updated=c._states["sensor.p"].last_updated)})
        self.go(c.async_evaluate())
        self.assertEqual(len(c.dongle.writes), 1)
        c = self.make(5.0, soc_entity="sensor.s", soc_high=97, soc_export_w=50)
        self.sensors(c, **{"sensor.s": SimpleNamespace(state="95", attributes={}, last_updated=c._states["sensor.p"].last_updated)})
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_grid_sign_option(self):
        c = self.make(5.0, grid_export_negative=False)  # -2000 now means importing
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_stale_option(self):
        c = self.make(5.0, stale_seconds=30)
        self.sensors(c, age=60)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_sun_elevation_option(self):
        c = self.make(5.0, min_sun_elevation=60)  # the sun is at 40
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_exit_hold_and_fast_exit_options(self):
        c = self.make(3.0, exit_hold_seconds=600)
        self.go(c.async_evaluate())
        self.set_price(c, 0.01)  # above the stop price, but not for long enough
        self.go(c.async_evaluate())
        self.assertTrue(c.curtailed)
        c = self.make(3.0, exit_hold_seconds=600, fast_exit_price=0.02)
        self.go(c.async_evaluate())
        self.set_price(c, 0.03)  # above the fast exit price: no waiting
        self.go(c.async_evaluate())
        self.assertFalse(c.curtailed)
        self.assertEqual(c.dongle.limit, 3.0)

    def test_block_gate_option(self):
        gate = lambda c, state: self.sensors(c, **{"select.mode": SimpleNamespace(
            state=state, attributes={}, last_updated=c._states["sensor.p"].last_updated)})
        c = self.make(5.0, block_entity="select.mode", block_state="force_charge")
        gate(c, "force_charge")
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])  # blocked: does not start
        gate(c, "normal")
        self.go(c.async_evaluate())
        self.assertTrue(c.curtailed)
        gate(c, "Force_Charge")
        self.go(c.async_evaluate())  # blocked while limiting: put the original back
        self.assertFalse(c.curtailed)
        self.assertEqual(c.dongle.limit, 5.0)

    def test_unreadable_gate_blocks_starting(self):
        c = self.make(5.0, block_entity="select.mode", block_state="force_charge")
        self.go(c.async_evaluate())  # the gate entity does not exist
        self.assertEqual(c.dongle.writes, [])

    def test_battery_power_option_raises_the_limit(self):
        c = self.make(5.0, battery_power_entity="sensor.b", battery_charge_w=150)
        batt = lambda v: self.sensors(c, **{"sensor.b": SimpleNamespace(
            state=str(v), attributes={"unit_of_measurement": "W"}, last_updated=c._states["sensor.p"].last_updated)})
        batt(0)
        self.go(c.async_evaluate())
        self.grid, self.ac = 0.0, 950  # no export any more, battery soaking up 1 kW
        c.data = {**c.data, "ac_w": 950}
        batt(1000)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95, 2.15])  # 0.95 + 1.0 + 0.2 margin
        c2 = self.make(5.0, battery_power_entity="sensor.b", battery_charge_positive=False)  # sign flipped
        self.assertEqual(c2.settings.battery_charge_w, 150.0)

    def test_lockout_minutes_option(self):
        c = self.make(3.0, lockout_minutes=60)
        self.go(c.async_evaluate())
        c._claim["started"] = time.time() - 5 * 3600
        self.go(c.async_evaluate())  # max duration stop
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())  # conditions clear, but the lockout has not run out
        self.set_price(c, -0.01)
        c._last_change = -1e9
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95, 3.0])
        c._lock_until = 0.0  # the hour is over
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes[-1], 0.95)

    def test_deadband_and_interval_options(self):
        c = self.make(5.0, deadband_w=5000)
        self.go(c.async_evaluate())
        self.grid, self.ac = 500.0, 950
        c.data = {**c.data, "ac_w": 950}
        self.sensors(c)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95])  # the +0.7 kW raise is inside the huge deadband

    def test_defaults_come_from_const_and_options_override_them(self):
        c = self.make(5.0)
        self.assertEqual(c.opt("export_seen_w"), 40)
        c.entry.options = {"export_seen_w": 500}
        self.assertEqual(c.settings.export_seen_w, 500.0)

    # --- shadow mode ---

    def test_shadow_mode_reports_and_writes_nothing(self):
        c = self.make(5.0, shadow_mode=True)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])
        self.assertFalse(c.curtailed)
        self.assertEqual(c.target_kw, 0.95)
        self.assertEqual(c.events[-1]["event"], "start")
        self.assertEqual(c.events[-1]["mode"], "shadow")
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.events[-1]["event"], "end")
        self.assertEqual(c.dongle.writes, [])

    def test_turning_shadow_mode_on_puts_the_original_back(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.entry.data["shadow_mode"] = True
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.limit, 3.0)
        self.assertFalse(c.curtailed)

    # --- restart while curtailed ---

    def test_restart_while_curtailed_still_restores_the_original(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        fresh = self.make(0.95)  # new process: only the store survives
        fresh.dongle = c.dongle
        self.go(fresh.async_load_claim())
        self.assertTrue(fresh.curtailed)
        self.assertEqual(fresh.original_kw, 3.0)
        self.set_price(fresh, 0.1)
        self.go(fresh.async_evaluate())
        self.assertEqual(c.dongle.limit, 3.0)

    # --- someone else changed the limit while curtailed ---

    def test_limit_changed_by_someone_else_is_not_clobbered(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.dongle.limit = 1.2  # operator sets a new cap
        c.data = {**c.data, "limit_kw": 1.2}
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95])  # nothing more written
        self.assertEqual(c.dongle.limit, 1.2)
        self.assertFalse(c.curtailed)

    def test_change_between_polls_is_caught_by_the_fresh_read(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.dongle.limit = 1.2  # polled data still says 0.95
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.limit, 1.2)
        self.assertFalse(c.curtailed)

    def test_limit_set_back_by_someone_else_just_drops_the_claim(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.dongle.limit = 3.0
        c.data = {**c.data, "limit_kw": 3.0}
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.95])
        self.assertFalse(c.curtailed)


if __name__ == "__main__":
    unittest.main()
