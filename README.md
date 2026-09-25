# airsupply

Monitor a ResMed AirMini CPAP in Home Assistant, over Bluetooth Classic.

Each machine becomes a device belonging to one of your people: therapy hours,
last used, state, therapy mode, pressures. It reads; it never writes.

Bluetooth Classic, not BLE, so the host must be within about ten metres of the
machine.

## Install

[![Add repository to your Home Assistant instance](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fananthb%2Fhass-addons)

Add `https://github.com/ananthb/hass-addons` as an add-on repository, install
**airsupply**, open it from the sidebar. Install the Mosquitto broker add-on
too; that is how the sensors get out. [Add-on docs][docs].

[docs]: https://github.com/ananthb/hass-addons/blob/main/airsupply/DOCS.md

## The card

Fourteen nights of therapy as bars against the four-hour mark. Install through
HACS as a Dashboard repository pointing here, or copy
`card/airsupply-card.js` into `config/www/` and add it as a resource.

```yaml
type: custom:airsupply-card
entity: sensor.airsupply_<machine>_therapy_hours
```

Options: `nights` (14), `goal` (4), `title`, `only_owner`. With
`only_owner: true` the card shows only to the user the machine is assigned to.

`card/preview.html` opens in any browser with invented data.

## Layout

| | |
|---|---|
| `airsupply/` | the add-on image |
| `card/` | the Lovelace card |
| `docs/protocol.md` | the transport, and what is known versus assumed |
| `docs/verify.md` | what is confirmed against a real machine |

## Licence

GPL-3.0-only. [libairmini][lib] (BSD-2-Clause) owns the protocol and is built
from the commit in [`airsupply/libairmini.pin`](airsupply/libairmini.pin),
not vendored.

[lib]: https://github.com/psychoticbeef/libairmini
