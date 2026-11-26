# Sungrow export limit

[![Validate](https://github.com/luizbon/ha-sungrow-export-limit/actions/workflows/validate.yml/badge.svg)](https://github.com/luizbon/ha-sungrow-export-limit/actions/workflows/validate.yml)

A Home Assistant integration that limits a Sungrow inverter's feed-in limit while exporting costs you
money, and tunes that limit to your house so it cuts the export without cutting your own supply. It
looks at a price sensor and a grid power sensor, and it only ever undoes its own change.

It talks to the WiNet dongle over your network. There is no cloud and no dongle login.

## Why it exists

Retailers with spot pricing, such as Amber in Australia, sometimes charge you to export. A battery
controller can only steer the battery. It cannot stop an AC-coupled solar inverter from pushing power
to the grid. The inverter has its own feed-in limit, and this integration writes it.

Home Assistant cannot reach that limit on its own. The dongle's Modbus port (502) is wrapped in AES by
the inverter, so a plain Modbus client gets nothing back. This integration speaks that protocol itself.

## Install

**HACS (recommended):** open HACS, search for "Sungrow export limit", download it, and restart Home
Assistant.

**HACS custom repository (fallback):** if the search doesn't find it, add this repository as a custom
repository (category Integration), then download it and restart Home Assistant.

**Manual:** copy `custom_components/sungrow_export_limit` into your `config/custom_components` folder
and restart.

Then go to Settings, Devices & services, Add integration, and search for "Sungrow export limit".

## How the rule works

Some Sungrow inverters, such as the SG5K-D, have no meter. Register 5010 then caps the inverter's total
AC output, not the grid export, so a blunt limit of 0 also cuts the power your own house is using. The rule
therefore reads a grid sensor from another device (a smart meter or a hybrid inverter's CT) and closes the loop.

**Starting.** Limiting starts only when all of these have held for the hold time (2 minutes by default):

- the price is at or below the start price (default -0.005), and
- the site really exports at least 150 W, or the battery is at or above 93% and exports at least 50 W.

**While limiting.** About every 30 seconds the limit follows the house:

- if the site still exports, the limit is cut by that export (plus a 50 W margin);
- if the site imports, the limit is raised by that import (plus 200 W), because it was cut too far;
- if a battery power sensor is set and the battery charges hard, the limit is raised by that charging;
- it never goes below the house load (rounded up to 0.1 kW) or the minimum limit (default 0.3 kW), and never
  above the limit the inverter had before;
- changes smaller than the deadband (250 W) are ignored, and lowering waits at least 90 s after a write.
  Raising never waits.

**Stopping.** The original limit is put back when the price has stayed at or above the stop price (default
0.005, the gap to the start price is the hysteresis) for the hold time, or at once at the optional fast exit
price, when the optional gate entity blocks, when a needed sensor is missing or older than 5 minutes, the dongle cannot
be read, the sun drops below the cutoff elevation (12 degrees), the episode runs longer than 4 hours, or the
rule has nothing left to cut. After the 4 hour stop it does not start again until the conditions have cleared
once. The watchdog also puts the original limit back after 15 minutes unless the rule keeps renewing it.

## Shadow mode

A new setup starts in shadow mode. The rule evaluates everything and reports what it would do, and writes
nothing to the inverter. Watch `sensor.*_target_export_limit` (its attributes show the reason) and the
episode event below, and turn the `switch.*_shadow_mode` entity off when you trust it.

## Settings

Nothing the rule does is fixed in code: every threshold, time and margin below is a setting, set in the setup
form and changed later with the Configure button. The defaults suit a 5 kW inverter on spot pricing.
(What stays fixed is the dongle's protocol: the register addresses, the encryption key and the Modbus unit
id, because they are defined by the hardware.)

| Setting | Default | What it does |
|---|---|---|
| Dongle IP address | required | Change it later with Reconfigure |
| Dongle Modbus port | 502 | |
| Dongle connection timeout | 15 s | Per connection |
| Read the inverter every | 30 s | Also how often the rule runs. Applied after a reload |
| Feed-in price sensor | required | The price you are paid to export. Negative means you pay to export |
| Grid power sensor | required | Site grid power in W or kW, from a meter. The inverter has none |
| Grid sensor reports export as negative | on | The usual meter convention. Turn off if export reads positive |
| House load sensor | optional | Whole-house consumption. The limit never goes below it. Without it only the minimum limit is the floor, so set that near your usual load |
| Battery charge sensor | optional | Lets limiting start on a smaller export while the battery is full |
| Battery power sensor | optional | A battery charging harder than the threshold raises the limit by that much |
| Battery power reads positive while charging | on | Turn off if charging reads negative |
| Battery charging that raises the limit | 150 W | |
| Gate entity and gate state | optional | While the entity is in that state (ignoring case, for example a battery mode `force_charge`), limiting does not start and a running episode ends. An unknown or unavailable gate entity blocks starting |
| Shadow mode | on | Report what would happen, write nothing |
| Start limiting at or below this price | -0.005 | In the price sensor's own unit |
| Stop limiting at or above this price | 0.005 | Must be above the start price |
| Hold time before stopping | 120 s | The price must stay above the stop price this long. A missing price or sensor stops at once |
| Stop at once at or above this price | optional | Skips the hold time above. Empty turns it off |
| Grid export needed to start | 150 W | |
| Battery full level / export needed then | 93% / 50 W | |
| Hold time before starting | 120 s | Price and export must both hold this long |
| Export that is cut from the limit | 40 W | Below this the limit is left as it is |
| Import that raises the limit | 250 W | Above this the limit was cut too far |
| Extra cut margin / Raise margin | 50 W / 200 W | Added to the cut and to a raise |
| Round the limit to | 0.05 kW | |
| Round the house load floor up to | 0.1 kW | |
| Minimum limit | 0.3 kW | Nothing below this is ever written |
| Ignore changes smaller than | 250 W | Saves writes to the inverter |
| Minimum time between lowering writes | 90 s | Raising never waits |
| Longest single episode | 4 h | Then it restores and waits for the conditions to clear |
| Lockout after the longest episode | 0 min | Wait at least this long, and until the conditions have cleared once. 0 waits only for the conditions to clear |
| Distrust sensor readings older than | 300 s | Slow cloud sensors are fine up to this age |
| Sun entity and cutoff elevation | `sun.sun`, 12° | Restores before the dongle powers off |
| Put the original limit back after | 15 min | Watchdog: lapses unless the rule renews it |
| Retry a failed restore after | 60 s | |

The old price-only trigger (`trigger_price` and `max_export_kw`, and their number entities) is gone, as is
the "Export limit otherwise" setting. If you upgrade from an earlier version, open Configure once and pick
the grid sensor: until it is set, the rule has nothing to act on and writes nothing.

## Entities

- `switch.*_shadow_mode` turns shadow mode on and off.
- `sensor.*_target_export_limit` is the limit the rule wants while limiting (empty otherwise). The attributes
  give the mode, the reason for the last decision and the house load floor.
- `sensor.*_export_limit` shows the limit the inverter is using now.
- `binary_sensor.*_export_curtailed` is on while the integration holds a saved limit. Its `original_limit_kw`
  attribute is the value that will be put back.
- `sensor.*_ac_output_power` and `sensor.*_export_limit_ratio` report what the inverter is doing.

The integration fires a `sungrow_export_limit_episode` event when an episode starts, changes and ends, in
shadow mode too. It carries `event` (start, update or end), `mode` (shadow or armed), `reason`, `limit_kw`,
`original_kw`, `duration_min` and the price, export, load, battery and AC readings. Use it to send
notifications or keep a log.

## Safety

The integration writes one register (5010, the feed-in limit) and never outside 0 to the rated power.
It only undoes what it did itself, and it is built so a failure leaves you exporting, not stuck low.

- It never writes while the rule is not limiting. At setup, at the first price or sun change, or with an
  unavailable sensor, the limit on the inverter is left exactly as you or your installer set it. In shadow
  mode it never writes at all.
- When the rule first limits, it reads the limit from the inverter and saves it (in Home Assistant's
  storage, so it survives a restart). If that read fails, it does not limit.
- It never raises a limit above what it found. If the limit is already at or below what the rule wants (for
  example an installer cap), nothing is written.
- Restoring writes that saved value, not the rated power. It happens on every stop condition above, when the
  integration is disabled or removed, or when Home Assistant stops. A restart while limiting still restores it.
- Before changing or restoring, it reads the limit again. If something else changed it while the integration
  held it down (you, the installer's tools, another integration), that is no longer the integration's to
  undo: it logs a warning, forgets the saved value and writes nothing. If the limit was set back to the saved
  value already, it just forgets it. If the rule is still triggered afterwards, it may start again, and it
  then saves the new value it finds.
- Turning shadow mode on while limiting puts the original limit back first.

One case it cannot cover: if the dongle drops off the network while the limit is lowered, nothing can
write the restore until the dongle returns. Keep the sun cutoff generous, because the dongle powers off
with the inverter at the end of the day.

## Compatibility

Tested on an SG5K-D behind a WiNet-S dongle. Other Sungrow models that use the same registers (5009,
5010 and 5014) may work. Reports are welcome in the issue tracker.

Whether the limit caps real grid export, or only the inverter's output, depends on whether your site has
a meter the inverter can see. Without one it caps the inverter's output, which is why the rule needs a grid
sensor from another device and tracks the house load. Check it on your own system, in shadow mode first.

## Development

`python3 -I -m unittest discover -s tests` runs the tests. They need `pycryptodomex` and use a fake
encrypted dongle on loopback, so no hardware is required.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by Sungrow. Changing an
inverter's settings is your responsibility. The software comes without warranty.
