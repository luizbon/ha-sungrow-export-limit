"""The setup and options forms must serialise with real Home Assistant code (the frontend does this).

Skipped when homeassistant is not installed. CI installs it.
"""
import os, sys, unittest

try:
    from homeassistant.helpers import config_validation as cv
    HAVE_HA = True
except ImportError:
    HAVE_HA = False

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


@unittest.skipUnless(HAVE_HA, "homeassistant not installed")
class FormsSerialise(unittest.TestCase):
    def setUp(self):
        from custom_components.sungrow_export_limit import config_flow
        self.flow = config_flow

    def _fields(self, current):
        schema = self.flow._rule_schema(current)
        try:
            import probatio  # HA 2026.11 and later
            return probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
        except ImportError:
            import voluptuous_serialize  # older HA
            return voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)

    def test_options_form_with_saved_values(self):
        fields = self._fields({"price_entity": "sensor.price", "trigger_price": 0.02, "max_export_kw": 1.5})
        names = {f["name"] for f in fields}
        self.assertTrue({"price_entity", "trigger_price", "max_export_kw", "full_kw"} <= names)

    def test_setup_form_with_nothing_saved(self):
        self.assertTrue(self._fields({}))

    def test_no_selector_gets_a_none_unit(self):
        for f in self._fields({}):
            unit = f.get("selector", {}).get("number", {}).get("unit_of_measurement", "")
            self.assertIsNotNone(unit, f["name"])

    def test_rule_check_rejects_max_export_not_below_full(self):
        bad = {self.flow.CONF_MAX_EXPORT_KW: 5.0, self.flow.CONF_FULL_KW: 5.0}
        self.assertTrue(self.flow._check_rule(bad))
        self.assertFalse(self.flow._check_rule({self.flow.CONF_MAX_EXPORT_KW: 0.0, self.flow.CONF_FULL_KW: 5.0}))
        self.assertFalse(self.flow._check_rule({self.flow.CONF_MAX_EXPORT_KW: 0.0}))


if __name__ == "__main__":
    unittest.main()
