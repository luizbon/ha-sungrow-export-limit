import importlib.util, os, unittest

spec = importlib.util.spec_from_file_location(
    "policy", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components",
                           "sungrow_export_limit", "policy.py"))
policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)

BASE = dict(price=-0.01, trigger_price=0.0, sun_elevation=40, min_elevation=12, limit_kw=5.0,
            triggered_kw=0.0, original_kw=None, seconds_since_change=999, min_interval=120)
HELD = dict(original_kw=5.0, limit_kw=0.0)  # we lowered 5.0 to 0.0 and still hold it


def d(**kw):
    return policy.decide(**{**BASE, **kw})


class NotCurtailing(unittest.TestCase):
    def test_price_below_trigger_curtails(self):
        self.assertEqual(d(), 0.0)

    def test_writes_nothing_when_not_triggered(self):
        for kw in (dict(price=0), dict(price=0.08), dict(price=None), dict(sun_elevation=5),
                   dict(sun_elevation=None)):
            for limit in (5.0, 3.0, 0.0):  # whatever the inverter already has
                self.assertIsNone(d(limit_kw=limit, **kw), (kw, limit))

    def test_never_raises_a_lower_existing_limit(self):
        self.assertIsNone(d(limit_kw=1.0, triggered_kw=1.5))  # installer cap below our target
        self.assertIsNone(d(limit_kw=1.5, triggered_kw=1.5))
        self.assertIsNone(d(limit_kw=0.0, triggered_kw=0.0))

    def test_custom_trigger_price(self):
        self.assertEqual(d(price=0.03, trigger_price=0.05), 0.0)
        self.assertIsNone(d(price=0.06, trigger_price=0.05))

    def test_custom_triggered_value(self):
        self.assertEqual(d(triggered_kw=1.5), 1.5)

    def test_custom_min_elevation(self):
        self.assertIsNone(d(sun_elevation=20, min_elevation=25))
        self.assertEqual(d(sun_elevation=25, min_elevation=25), 0.0)

    def test_lowering_waits_out_flap_guard(self):
        self.assertIsNone(d(seconds_since_change=10))
        self.assertEqual(d(seconds_since_change=10, min_interval=5), 0.0)


class Curtailing(unittest.TestCase):
    def test_holding_while_triggered_writes_nothing(self):
        self.assertIsNone(d(**HELD))

    def test_restores_the_original_not_a_default(self):
        self.assertEqual(d(price=0.01, **{**HELD, "original_kw": 3.0}), 3.0)

    def test_unknown_price_or_sun_restores_the_original(self):
        self.assertEqual(d(price=None, **HELD), 5.0)
        self.assertEqual(d(sun_elevation=5, **HELD), 5.0)
        self.assertEqual(d(sun_elevation=None, **HELD), 5.0)

    def test_raising_never_waits(self):
        self.assertEqual(d(price=0.1, seconds_since_change=1, **HELD), 5.0)

    def test_retargeting_follows_the_setting_but_never_above_the_original(self):
        self.assertEqual(d(triggered_kw=1.0, original_kw=5.0, limit_kw=2.0, seconds_since_change=999), 1.0)
        self.assertEqual(d(triggered_kw=9.0, original_kw=5.0, limit_kw=2.0), 5.0)

    def test_lowering_further_waits_out_flap_guard(self):
        self.assertIsNone(d(triggered_kw=1.0, original_kw=5.0, limit_kw=2.0, seconds_since_change=10))


if __name__ == "__main__":
    unittest.main()
