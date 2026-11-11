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


@unittest.skipUnless(HAVE_HA, "homeassistant not installed")
class OptionsFlowHost(unittest.TestCase):
    """Drive the real options flow with a stub entry."""

    def _flow(self, host="192.0.2.10"):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        from custom_components.sungrow_export_limit import config_flow as cf
        entry = SimpleNamespace(entry_id="e1", data={"host": host, "price_entity": "sensor.price"}, options={})
        flow = cf.SungrowOptionsFlow()
        flow.hass = mock.MagicMock()
        flow.hass.config_entries.async_get_known_entry.return_value = entry
        flow.handler = "e1"
        return cf, flow, entry, asyncio

    def test_form_shows_the_dongle_address_first(self):
        import probatio
        cf, flow, entry, asyncio = self._flow()
        result = asyncio.run(flow.async_step_init())
        fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
        self.assertEqual(fields[0]["name"], "host")

    def _submit(self, new_host, probe_errors):
        from unittest import mock
        cf, flow, entry, asyncio = self._flow()
        values = {"host": new_host, "price_entity": "sensor.price", "trigger_price": 0.0, "max_export_kw": 0.0,
                  "sun_entity": "sun.sun", "min_sun_elevation": 12, "restore_after_minutes": 15,
                  "min_interval_seconds": 120}

        async def probe(*_a):
            return probe_errors
        with mock.patch.object(cf, "_probe", probe):
            return flow, entry, asyncio.run(flow.async_step_init(values))

    def test_unreachable_new_address_is_rejected(self):
        flow, entry, result = self._submit("192.0.2.99", {"base": "cannot_connect"})
        self.assertEqual(result["type"].value, "form")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        flow.hass.config_entries.async_update_entry.assert_not_called()

    def test_new_address_is_saved_to_the_entry_not_the_options(self):
        flow, entry, result = self._submit("192.0.2.99", {})
        self.assertEqual(result["type"].value, "create_entry")
        self.assertNotIn("host", result["data"])
        kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        self.assertEqual(kwargs["data"]["host"], "192.0.2.99")

    def test_unchanged_address_is_not_probed_or_rewritten(self):
        flow, entry, result = self._submit("192.0.2.10", {"base": "cannot_connect"})
        self.assertEqual(result["type"].value, "create_entry")
        flow.hass.config_entries.async_update_entry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
