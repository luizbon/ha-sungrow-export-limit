import importlib.util, os, unittest

spec = importlib.util.spec_from_file_location(
    "policy", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components",
                           "sungrow_export_limit", "policy.py"))
policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)

BASE = dict(price=-0.01, trigger_price=0.0, sun_elevation=40, min_elevation=12, limit_kw=5.0,
            triggered_kw=0.0, full_kw=5.0, seconds_since_change=999, min_interval=120)


def d(**kw):
    return policy.decide(**{**BASE, **kw})


class Rule(unittest.TestCase):
    def test_price_below_trigger_curtails(self):
        self.assertEqual(d(), 0.0)

    def test_price_at_or_above_trigger_stays_full(self):
        self.assertIsNone(d(price=0))
        self.assertIsNone(d(price=0.08))

    def test_custom_trigger_price(self):
        self.assertEqual(d(price=0.03, trigger_price=0.05), 0.0)
        self.assertIsNone(d(price=0.06, trigger_price=0.05))

    def test_custom_triggered_and_full_values(self):
        self.assertEqual(d(triggered_kw=1.5), 1.5)
        self.assertEqual(d(price=0.1, limit_kw=0.0, full_kw=4.0), 4.0)
        self.assertIsNone(d(price=0.1, limit_kw=4.0, full_kw=4.0))

    def test_price_recovering_restores_full(self):
        self.assertEqual(d(price=0.01, limit_kw=0.0), 5.0)

    def test_unknown_price_restores_full(self):
        self.assertEqual(d(price=None, limit_kw=0.0), 5.0)

    def test_end_of_light_restores_and_blocks_curtailing(self):
        self.assertEqual(d(limit_kw=0.0, sun_elevation=5), 5.0)
        self.assertIsNone(d(sun_elevation=5))
        self.assertEqual(d(limit_kw=0.0, sun_elevation=None), 5.0)

    def test_custom_min_elevation(self):
        self.assertIsNone(d(sun_elevation=20, min_elevation=25))
        self.assertEqual(d(sun_elevation=25, min_elevation=25), 0.0)

    def test_raising_never_waits(self):
        self.assertEqual(d(price=0.1, limit_kw=0.0, seconds_since_change=1), 5.0)

    def test_lowering_waits_out_flap_guard(self):
        self.assertIsNone(d(seconds_since_change=10))
        self.assertEqual(d(seconds_since_change=10, min_interval=5), 0.0)

    def test_any_other_value_is_corrected(self):
        self.assertEqual(d(price=0.1, limit_kw=2.5), 5.0)


if __name__ == "__main__":
    unittest.main()
