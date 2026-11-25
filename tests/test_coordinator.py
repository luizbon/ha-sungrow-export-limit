"""The coordinator must only undo what it did itself. Runs the real coordinator against a fake dongle."""
import asyncio, os, sys, unittest
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
        self.price, self.sun = -0.01, 40

    def make(self, limit=5.0, max_export=0.0):
        """A coordinator whose polled data is `limit`, as after a restart or a poll."""
        co = self.co
        c = object.__new__(co.LimitCoordinator)
        states = {"sensor.p": SimpleNamespace(state=str(self.price), attributes={}),
                  "sun.sun": SimpleNamespace(state="x", attributes={"elevation": self.sun})}
        c.hass = SimpleNamespace(states=SimpleNamespace(get=states.get), async_add_executor_job=self._run)
        c.entry = SimpleNamespace(entry_id="e1", options={}, data={
            "host": "h", "price_entity": "sensor.p", "trigger_price": 0.0, "max_export_kw": max_export,
            "sun_entity": "sun.sun", "min_sun_elevation": 12, "restore_after_minutes": 15,
            "min_interval_seconds": 120})
        c.dongle = FakeDongle(limit)
        c._states = states
        c._cancel_restore, c._last_change, c._lock = None, -1e9, asyncio.Lock()
        c._store, c._claim = FakeStore(), None
        c.data = {"nominal_kw": 5.0, "limit_kw": limit}

        async def refresh():
            c.data = {**c.data, "limit_kw": c.dongle.limit}
        c.async_set_updated_data = lambda data: setattr(c, "data", data)
        c.async_request_refresh = refresh
        return c

    async def _run(self, fn, *args):
        return fn(*args)

    def go(self, coro):
        return asyncio.run(coro)

    def set_price(self, c, price):
        c._states["sensor.p"] = SimpleNamespace(state=str(price), attributes={})

    # --- nothing is written unless we curtail ---

    def test_no_trigger_writes_nothing_even_with_a_lower_limit_on_the_inverter(self):
        self.price = 0.1
        for limit in (5.0, 2.0):
            c = self.make(limit)
            self.go(c.async_evaluate())
            self.assertEqual(c.dongle.writes, [])
            self.assertFalse(c.curtailed)

    def test_unavailable_price_writes_nothing(self):
        c = self.make(2.0)
        c._states["sensor.p"] = SimpleNamespace(state="unavailable", attributes={})
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])

    def test_does_not_raise_a_limit_already_below_the_target(self):
        c = self.make(1.0, max_export=1.5)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [])
        self.assertFalse(c.curtailed)

    # --- curtail, remember, restore ---

    def test_curtail_stores_what_it_read_and_restores_that(self):
        c = self.make(3.0)  # installer cap of 3 kW
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.0])
        self.assertEqual(FakeStore.saved["v"], {"original_kw": 3.0, "applied_kw": 0.0})
        self.assertTrue(c.curtailed)
        self.set_price(c, 0.1)
        self.go(c.async_evaluate())
        self.assertEqual(c.dongle.writes, [0.0, 3.0])  # 3 kW, not rated power (5 kW)
        self.assertFalse(c.curtailed)
        self.assertEqual(FakeStore.saved, {})

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
        self.assertEqual(c.dongle.limit, 0.0)  # ...and the rule curtails again from a fresh read
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

    # --- restart while curtailed ---

    def test_restart_while_curtailed_still_restores_the_original(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        fresh = self.make(0.0)  # new process: only the store survives
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
        self.assertEqual(c.dongle.writes, [0.0])  # nothing more written
        self.assertEqual(c.dongle.limit, 1.2)
        self.assertFalse(c.curtailed)

    def test_change_between_polls_is_caught_by_the_fresh_read(self):
        c = self.make(3.0)
        self.go(c.async_evaluate())
        c.dongle.limit = 1.2  # polled data still says 0.0
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
        self.assertEqual(c.dongle.writes, [0.0])
        self.assertFalse(c.curtailed)


if __name__ == "__main__":
    unittest.main()
