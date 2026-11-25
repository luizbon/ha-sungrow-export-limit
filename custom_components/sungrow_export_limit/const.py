DOMAIN = "sungrow_export_limit"

CONF_PRICE_ENTITY = "price_entity"
CONF_GRID_ENTITY = "grid_entity"  # grid power; export is negative unless CONF_GRID_EXPORT_NEGATIVE is off
CONF_GRID_EXPORT_NEGATIVE = "grid_export_negative"
CONF_LOAD_ENTITY = "load_entity"  # optional: whole-house consumption, sets the floor of the limit
CONF_SOC_ENTITY = "soc_entity"  # optional: battery state of charge
CONF_ENTER_PRICE = "enter_price"  # start limiting while the price is at or below this
CONF_EXIT_PRICE = "exit_price"  # stop limiting once the price is at or above this
CONF_MIN_EXPORT_W = "min_export_w"
CONF_SOC_HIGH = "soc_high"
CONF_SOC_EXPORT_W = "soc_export_w"
CONF_HOLD_SECONDS = "hold_seconds"
CONF_MIN_LIMIT_KW = "min_limit_kw"
CONF_DEADBAND_W = "deadband_w"
CONF_MAX_HOURS = "max_hours"
CONF_STALE_SECONDS = "stale_seconds"
CONF_SHADOW = "shadow_mode"
CONF_SUN_ENTITY = "sun_entity"
CONF_MIN_ELEVATION = "min_sun_elevation"
CONF_RESTORE_MINUTES = "restore_after_minutes"
CONF_MIN_INTERVAL = "min_interval_seconds"

CONF_BATTERY_POWER_ENTITY = "battery_power_entity"  # optional: lets the limit rise by what the battery absorbs
CONF_BATTERY_CHARGE_POSITIVE = "battery_charge_positive"
CONF_BATTERY_CHARGE_W = "battery_charge_w"
CONF_BLOCK_ENTITY = "block_entity"  # optional gate, for example a battery mode sensor
CONF_BLOCK_STATE = "block_state"
CONF_FAST_EXIT_PRICE = "fast_exit_price"  # optional: at or above this price, stop without waiting out the exit hold
CONF_EXIT_HOLD_SECONDS = "exit_hold_seconds"
CONF_EXPORT_SEEN_W = "export_seen_w"
CONF_IMPORT_SEEN_W = "import_seen_w"
CONF_CUT_MARGIN_W = "cut_margin_w"
CONF_RAISE_MARGIN_W = "raise_margin_w"
CONF_STEP_KW = "step_kw"
CONF_LOAD_ROUND_KW = "load_round_kw"
CONF_LOCKOUT_MINUTES = "lockout_minutes"
CONF_RETRY_SECONDS = "retry_seconds"
CONF_SCAN_SECONDS = "scan_seconds"
CONF_PORT = "port"
CONF_TIMEOUT = "timeout"

OPTIONAL_ENTITIES = (CONF_LOAD_ENTITY, CONF_SOC_ENTITY, CONF_BATTERY_POWER_ENTITY, CONF_BLOCK_ENTITY)
OPTIONAL_VALUES = (CONF_FAST_EXIT_PRICE, CONF_BLOCK_STATE)  # a cleared field must not fall back to setup data
EVENT_EPISODE = f"{DOMAIN}_episode"

DEFAULTS = {
    CONF_GRID_EXPORT_NEGATIVE: True,  # the usual meter convention: positive imports, negative exports
    CONF_ENTER_PRICE: -0.005,
    CONF_EXIT_PRICE: 0.005,
    CONF_MIN_EXPORT_W: 150,  # grid export that has to be seen before limiting starts
    CONF_SOC_HIGH: 93,  # a full battery cannot absorb the surplus, so a smaller export is enough
    CONF_SOC_EXPORT_W: 50,
    CONF_HOLD_SECONDS: 120,  # the start condition must hold this long
    CONF_MIN_LIMIT_KW: 0.3,  # never write below this
    CONF_DEADBAND_W: 250,  # ignore smaller changes of the target
    CONF_MAX_HOURS: 4.0,  # one episode never limits for longer than this
    CONF_STALE_SECONDS: 300,  # a price, grid, load or battery reading older than this is not trusted
    CONF_BATTERY_CHARGE_POSITIVE: True,  # battery power reads positive while charging
    CONF_BATTERY_CHARGE_W: 150,  # a battery charging harder than this means the limit can rise by that much
    CONF_BLOCK_STATE: "",  # the gate blocks while the entity is in this state (empty with an entity set: never)
    CONF_EXIT_HOLD_SECONDS: 120,  # the stop price must hold this long (the fast exit price skips it)
    CONF_EXPORT_SEEN_W: 40,  # export above this is cut from the limit
    CONF_IMPORT_SEEN_W: 250,  # import above this means the limit was cut too far and is raised
    CONF_CUT_MARGIN_W: 50,  # extra cut below the export seen
    CONF_RAISE_MARGIN_W: 200,  # extra headroom when raising
    CONF_STEP_KW: 0.05,  # the target is rounded to this step
    CONF_LOAD_ROUND_KW: 0.1,  # the house load floor is rounded up to this step
    CONF_LOCKOUT_MINUTES: 0,  # after a maximum duration stop, wait at least this long (0 = until conditions clear)
    CONF_RETRY_SECONDS: 60,  # retry a failed restore after this long
    CONF_SCAN_SECONDS: 30,  # how often the inverter is read, and so how often the rule runs
    CONF_PORT: 502,
    CONF_TIMEOUT: 15,  # seconds for each connection to the dongle
    CONF_SHADOW: True,  # compute and report, write nothing, until you turn this off
    CONF_SUN_ENTITY: "sun.sun",
    CONF_MIN_ELEVATION: 12,  # degrees; below this the light is ending and the dongle will power off
    CONF_RESTORE_MINUTES: 15,  # a limit we lowered lapses back unless the rule keeps renewing it
    CONF_MIN_INTERVAL: 90,  # seconds between writes that lower the limit
}
