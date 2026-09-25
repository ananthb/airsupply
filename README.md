# airsupply

Local control and data export for the **ResMed AirMini** CPAP.

The official app configures the machine and shows you a night's therapy, and
lets you export none of it. This repo is the two consumers that fix that: a
Home Assistant add-on that keeps the long-term record at home, and a phone app
that can push to Home Assistant, Health Connect and Apple Health.

**Status: it monitors.** The transport is understood, the protocol comes from
[libairmini](#the-protocol-is-not-ours), and the Home Assistant add-on pairs
with a real AirMini, reads it on a schedule, and publishes what it finds over
MQTT as a device per machine: therapy hours, when it was last used, the
machine's state, and the pressures it is set to. Each machine is tied to one
of Home Assistant's people by their id rather than their name, so renaming
somebody in Home Assistant does not orphan their machine.

Nothing is written to the machine. [`docs/verify.md`](docs/verify.md) records
what has been confirmed against hardware and what has not; the gaps left are
a night's logged data, the 25 Hz stream, and the `Set` path.

## It is not a BLE device

Worth stating first, because almost every guide you will find for putting a
gadget into Home Assistant assumes Bluetooth Low Energy, and all of it is wrong
here.

The AirMini speaks **Bluetooth Classic RFCOMM/SPP**. ResMed's own Android app
calls `listenUsingRfcommWithServiceRecord` with the Serial Port Profile UUID
`00001101-0000-1000-8000-00805F9B34FB` and contains no uses of `BluetoothGatt`
at all. On iOS it is an MFi/iAP2 accessory on protocol `com.resmed.rpc`.

What that rules out:

| | why |
|---|---|
| Web Bluetooth | GATT-only. No browser can open an RFCOMM channel, at any flag. |
| ESPHome `bluetooth_proxy` | proxies BLE only, so it cannot extend range here |
| Home Assistant's `bluetooth` integration | BLE-only, built on bleak |
| An iOS app on the App Store | `com.resmed.rpc` needs Apple MFi authorization |

The practical consequence is that **something with a Bluetooth Classic radio
has to sit within a few metres of the machine** — which, for a CPAP, means the
bedroom.

## The protocol is not ours

[`psychoticbeef/libairmini`](https://github.com/psychoticbeef/libairmini)
(BSD-2-Clause) reverse-engineered it from the official Android app and verified
it against real hardware: NCP framing, SRP-6a pairing with the device PIN,
no-PIN reconnect, the AES-256-CBC session, and the read methods including 25 Hz
flow and pressure streaming.

It is written to be embedded — no threading, no Bluetooth, with transport and
crypto injected by the host — so airsupply consumes it as C from both sides
rather than reimplementing it:

```
libairmini (C, BSD-2)
   ├── ctypes   ──►  Home Assistant add-on (Python)
   └── dart:ffi ──►  phone app (Flutter)
```

**What is ours** is the part libairmini explicitly left *"for a later,
carefully-verified pass"*: the `Set` / configuration write path. That work
belongs upstream, in C, under libairmini's BSD-2 — not here.

## Install

[![Add repository to your Home Assistant instance](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fananthb%2Fhass-addons)

The add-on is packaged in [ananthb/hass-addons](https://github.com/ananthb/hass-addons).
Add `https://github.com/ananthb/hass-addons` as an add-on repository
(**Settings → Add-ons → Add-on Store → ⋮ → Repositories**), then install
**airsupply**. It opens as a page in the Home Assistant sidebar, which sets the
machine up once and shows what it last read. See the
[add-on docs](https://github.com/ananthb/hass-addons/blob/main/airsupply/DOCS.md).

## Layout

| | |
|---|---|
| `docs/protocol.md` | the transport, and what is known versus assumed |
| `docs/verify.md` | what is confirmed against a real machine, and what is not |
| `airsupply/` | the container image behind the Home Assistant add-on |
| `card/` | the Lovelace card: hours per night, against the four-hour mark |

`airsupply/` builds libairmini from the commit in `libairmini.pin`, wraps it
for Python, and serves the page: an [Elm](https://elm-lang.org) application
over a small JSON API, compiled in the image so that a page which does not
compile fails the build. It is published as
`ghcr.io/ananthb/airsupply:v<version>` on every `v*` tag, and the add-on in
hass-addons is that image plus a run script and an AppArmor profile.

It writes nothing, and refuses to: `Set` is not among the methods it will send.

The phone app arrives once `docs/verify.md` says it can.

## The card

`card/airsupply-card.js` draws each of the last fourteen nights as a bar
against the four hours everything clinical is measured by. Home Assistant
already has the data: therapy hours is a `total_increasing` sensor, so the
recorder keeps the increase per hour, and a night is the increase from one
midday to the next -- midday to midday rather than midnight to midnight,
because a night spans midnight and calendar days would cut every one in half.

Install it through HACS as a Dashboard repository pointing at this one, or
copy the file into `config/www/` and add it as a resource. Then:

```yaml
type: custom:airsupply-card
entity: sensor.airsupply_<machine>_therapy_hours
```

`nights` (14), `goal` (4) and `title` are the options. Everything else about
the machine -- its state, whether it is in therapy, when it was last used --
is found from that one entity.

### Only on its owner's dashboard

```yaml
type: custom:airsupply-card
entity: sensor.airsupply_<machine>_therapy_hours
only_owner: true
```

The card then appears only for the Home Assistant user the machine is
assigned to on the add-on's page. It follows that assignment rather than
needing user ids typed in here, so reassigning a machine moves its card. A
machine nobody owns stays visible, because it is not somebody else's.

**This hides a card; it is not a permission.** Home Assistant has no
per-entity access control, so anyone logged in can still read the sensors
from developer tools or the API. It keeps somebody else's therapy off your
dashboard. It does not keep it from them.

It carries no colours of its own: a card that ships a palette fights whatever
theme it lands in, so it uses Home Assistant's. `card/preview.html` opens in
any browser with invented data, for looking at it without a Home Assistant.

## Licence

GPL-3.0-only. See [`LICENSE`](LICENSE).

libairmini is not vendored. The add-on builds it from the commit named in
[`airsupply/libairmini.pin`](airsupply/libairmini.pin), so it stays BSD-2-Clause
in its own repository and a bump here is a one-line diff.
