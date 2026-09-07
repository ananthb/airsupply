# airsupply

Local control and data export for the **ResMed AirMini** CPAP.

The official app configures the machine and shows you a night's therapy, and
lets you export none of it. This repo is the two consumers that fix that: a
Home Assistant add-on that keeps the long-term record at home, and a phone app
that can push to Home Assistant, Health Connect and Apple Health.

**Status: nothing is monitored yet.** The transport is understood and the
protocol comes from [libairmini](#the-protocol-is-not-ours), but no code here
has yet read a byte from a real machine. What exists is a Home Assistant add-on
that runs the first experiments on your own hardware — can the host see the
machine, does the serial channel open, does pairing complete, and do the four
basic reads come back — and reports what it finds. It writes nothing. See
[`docs/verify.md`](docs/verify.md) for the full order.

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
**airsupply**. See the
[add-on docs](https://github.com/ananthb/hass-addons/blob/main/airsupply/DOCS.md)
for the three steps it walks you through.

## Layout

| | |
|---|---|
| `docs/protocol.md` | the transport, and what is known versus assumed |
| `docs/verify.md` | experiments against a real machine, in order, with results |
| `airsupply/` | the container image behind the Home Assistant add-on — **a diagnostic, not a monitor** |

`airsupply/` builds libairmini from the commit in `libairmini.pin`, wraps it
for Python, and is published as `ghcr.io/ananthb/airsupply:v<version>` on
every `v*` tag. The add-on in hass-addons is that image plus a run script, an
AppArmor profile and the options UI. It exists to answer experiments 1, 3 and
5 on your own hardware: whether the Home Assistant host can see the machine,
whether the serial channel opens and the SRP-6a pairing completes, and whether
the version, clock, settings and run-meter reads reproduce. It writes nothing,
and refuses to: `Set` is not among the methods it will send.

The phone app arrives once `docs/verify.md` says it can.

## Licence

GPL-3.0-only. See [`LICENSE`](LICENSE).

libairmini is not vendored. The add-on builds it from the commit named in
[`airsupply/libairmini.pin`](airsupply/libairmini.pin), so it stays BSD-2-Clause
in its own repository and a bump here is a one-line diff.
