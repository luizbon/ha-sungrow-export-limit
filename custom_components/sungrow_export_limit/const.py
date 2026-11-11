DOMAIN = "sungrow_export_limit"

CONF_PRICE_ENTITY = "price_entity"
CONF_TRIGGER_PRICE = "trigger_price"  # curtail while the price is below this
CONF_CURTAIL_KW = "curtail_kw"  # limit applied while triggered
CONF_FULL_KW = "full_kw"  # limit applied otherwise; blank = the inverter's rated power
CONF_SUN_ENTITY = "sun_entity"
CONF_MIN_ELEVATION = "min_sun_elevation"
CONF_RESTORE_MINUTES = "restore_after_minutes"
CONF_MIN_INTERVAL = "min_interval_seconds"

DEFAULTS = {
    CONF_TRIGGER_PRICE: 0.0,
    CONF_CURTAIL_KW: 0.0,
    CONF_SUN_ENTITY: "sun.sun",
    CONF_MIN_ELEVATION: 12,  # degrees; below this the light is ending and the dongle will power off
    CONF_RESTORE_MINUTES: 15,  # a curtailed limit lapses to full unless the rule keeps renewing it
    CONF_MIN_INTERVAL: 120,  # seconds between a change and the next curtail, to stop flapping
}
SCAN_SECONDS = 30
