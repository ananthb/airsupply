# airsupply

**This add-on does not monitor anything yet.** It is a diagnostic: it reports
what BlueZ can see of your ResMed AirMini, and optionally proves that the
serial channel to it opens. Nothing is read from the machine and nothing is
written to it.

## Why a diagnostic first

The AirMini speaks Bluetooth Classic RFCOMM, not BLE, so none of the usual Home
Assistant Bluetooth machinery reaches it. Three things had to be true before
writing a monitor, and only one of them was answerable from a desk:

1. The Home Assistant host has to be within about ten metres of the machine.
2. An add-on container has to be allowed to use Bluetooth Classic at all.
3. The serial channel has to open.

Number 2 is settled — see `docs/verify.md` in the repository. This add-on
answers 1 and 3 on your own hardware.

## Install

Add `https://github.com/ananthb/airsupply` as an add-on repository, then
install **airsupply**. It is built locally; there is no image to pull.

## Use

Start with defaults. `address` empty and `connect` off means survey only:

```yaml
log_level: info
address: ""
connect: false
```

Put the AirMini into pairing mode first — it does not stay discoverable, and
BlueZ can only report devices it has seen at least once. Then start the add-on
and read the log. You are looking for four things:

| | meaning |
|---|---|
| **Address** | the BD_ADDR to put in `address` |
| **Class** | present means Bluetooth Classic. Absent means BLE, and something is wrong with our understanding |
| **RSSI** | worse than about −80 dBm and a session will not hold |
| **SPP** | `Serial Port service present` confirms the transport |

If no candidate appears at all, the host is out of range and the add-on cannot
be the thing that talks to the machine.

## Opening the channel

The Bluetooth Classic bond has to exist first, and it is **not** the same as the
PIN pairing the AirMini's own app does. From the host, once:

```sh
bluetoothctl
  pair <address>
```

Then set `address` and turn `connect` on. Success looks like `Serial channel
open on fd N`, and that is where the add-on currently stops.

## What it deliberately does not do

Write. The `Set` path in the underlying protocol library is unverified, this is
a medical device, and the read path has to be proven first. See the repository's
`docs/verify.md` for the order.
