# Sungrow export limit

[![Validate](https://github.com/luizbon/ha-sungrow-export-limit/actions/workflows/validate.yml/badge.svg)](https://github.com/luizbon/ha-sungrow-export-limit/actions/workflows/validate.yml)

A Home Assistant integration that sets a Sungrow inverter's feed-in limit from a price sensor. The usual
use is to stop exporting while the feed-in price is negative and export freely the rest of the time.

It talks to the WiNet dongle over your network. There is no cloud and no dongle login.

## Why it exists

Retailers with spot pricing, such as Amber in Australia, sometimes charge you to export. A battery
controller can only steer the battery. It cannot stop an AC-coupled solar inverter from pushing power
to the grid. The inverter has its own feed-in limit, and this integration writes it.

Home Assistant cannot reach that limit on its own. The dongle's Modbus port (502) is wrapped in AES by
the inverter, so a plain Modbus client gets nothing back. This integration speaks that protocol itself.

## Install

HACS: add this repository as a custom repository (category Integration), install it, and restart
Home Assistant. Or copy `custom_components/sungrow_export_limit` into your `config/custom_components`
folder and restart.

Then go to Settings, Devices & services, Add integration, and search for "Sungrow export limit".

## Settings

Set these in the setup form and change them later with the Configure button.

| Setting | Default | What it does |
|---|---|---|
| Dongle IP address | required | Change it later with Reconfigure |
| Feed-in price sensor | required | Any sensor. A negative value means you pay to export |
| Trigger price | 0 | The rule fires while the sensor reads below this, in the sensor's own unit |
| Maximum export when triggered | 0 kW | The limit written once the trigger is hit. 0 stops export. A limit already at or below it is left alone |
| Sun entity | `sun.sun` | Used for the end-of-light cutoff |
| Stop limiting below this sun elevation | 12° | Puts the original limit back before the dongle powers off |
| Put the original limit back after | 15 min | A limit this integration lowered lapses back unless the rule renews it |
| Minimum time between limiting changes | 120 s | Stops the limit flapping on a noisy price |

The rule runs as soon as the integration is set up, but it writes nothing until the price actually
falls below the trigger. To pause it, disable the integration entry, which puts the original limit back.

## Entities

- `number.*_trigger_price` and `number.*_maximum_export_when_triggered` change those two settings live, for
  dashboards and automations. Set the trigger price above the current price to see the rule act.
- `sensor.*_export_limit` shows the limit the inverter is using now.
- `binary_sensor.*_export_curtailed` is on while the trigger is holding export down.
- `sensor.*_ac_output_power` and `sensor.*_export_limit_ratio` report what the inverter is doing.

## Safety

The integration writes one register (5010, the feed-in limit) and never outside 0 to the rated power.
It only undoes what it did itself, and it is built so a failure leaves you exporting, not stuck at zero.

- It never writes while the rule is not curtailing. At setup, at the first price or sun change, or with
  an unavailable price sensor, the limit on the inverter is left exactly as you or your installer set it.
- When the rule first curtails, it reads the limit from the inverter and saves it (in Home Assistant's
  storage, so it survives a restart). If that read fails, it does not curtail.
- It never raises a limit. If the limit is already at or below "Maximum export when triggered" (for
  example an installer cap), nothing is written.
- Restoring writes that saved value, not the rated power. It happens when the price recovers, the price
  or sun position becomes unknown, the sun is low, the restore time lapses, the integration is disabled
  or removed, or Home Assistant stops. A restart while curtailed still restores it.
- Before restoring, it reads the limit again. If something else changed it while the integration held
  it down (you, the installer's tools, another integration), that is no longer the integration's to undo:
  it logs a warning, forgets the saved value and writes nothing. If the limit was set back to the saved
  value already, it just forgets it. If the rule is still triggered afterwards, it may curtail again,
  and it then saves the new value it finds.
- `binary_sensor.*_export_curtailed` is on while the integration holds a saved limit, and its
  `original_limit_kw` attribute shows the value that will be put back.

One case it cannot cover: if the dongle drops off the network while the limit is curtailed, nothing can
write the restore until the dongle returns. Keep the sun cutoff generous, because the dongle powers off
with the inverter at the end of the day.

## Compatibility

Tested on an SG5K-D behind a WiNet-S dongle. Other Sungrow models that use the same registers (5009,
5010 and 5014) may work. Reports are welcome in the issue tracker.

Whether the limit caps real grid export, or only the inverter's output, depends on whether your site has
a meter the inverter can see. Check that on your own system before relying on it.

## Development

`python3 -I -m unittest discover -s tests` runs the tests. They need `pycryptodomex` and use a fake
encrypted dongle on loopback, so no hardware is required.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by Sungrow. Changing an
inverter's settings is your responsibility. The software comes without warranty.
