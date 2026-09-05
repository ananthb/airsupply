# airsupply

Local control and data export for the **ResMed AirMini** CPAP.

The official app configures the machine and shows you a night's therapy, and
lets you export none of it. This repo is the two consumers that fix that: a
Home Assistant add-on that keeps the long-term record at home, and a phone app
that can push to Home Assistant, Health Connect and Apple Health.

**Status: nothing works yet.** The repo exists, the transport is understood, and
the protocol comes from [libairmini](#the-protocol-is-not-ours). No code has run
against a machine from here. See [`docs/verify.md`](docs/verify.md) for the
first experiment, which decides whether the add-on is possible at all.

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

## Layout

| | |
|---|---|
| `docs/protocol.md` | the transport, and what is known versus assumed |
| `docs/verify.md` | experiments to run against a real machine, in order |

The add-on and the app arrive once `docs/verify.md` says they can.

## Licence

GPL-3.0-only. See [`LICENSE`](LICENSE).

Vendored libairmini sources, when they land under `vendor/`, stay BSD-2-Clause
with their copyright notice retained.
