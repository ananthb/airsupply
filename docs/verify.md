# Verification plan

Experiments against a real machine, in dependency order. Nothing here should be
skipped on the grounds that it is obviously fine — the whole project already
changed shape once on a transport assumption that turned out to be wrong.

Record results by editing this file. A failed experiment is a result.

---

## 1. Can the Home Assistant host see the machine?

**Blocks the add-on.** Bluetooth Classic on a PCB antenna is good for roughly
ten metres line-of-sight, and the machine is on a nightstand. No amount of
software fixes this one — it is the single fact that decides whether the add-on
is the primary consumer or the phone app is.

Put the AirMini into pairing mode first — it does not stay discoverable — then
from the *Advanced SSH & Web Terminal* add-on with protection mode **off**, so
you are on the host rather than in the add-on container:

```sh
bluetoothctl
  power on
  scan on
```

Expect the output to scroll: Home Assistant's own passive BLE scan is running
and every nearby beacon lands in it. **Do not fight it.** `scan off` will fail,
and a `transport bredr` filter will not quiet anything, because BlueZ discovery
sessions are per-D-Bus-client and the adapter takes the union of every client's
filter. Just read the cache afterwards:

```sh
bluetoothctl devices | grep -i -e resmed -e airmini
bluetoothctl info <BD_ADDR>
```

Record:

- [ ] Does an AirMini appear? Its name and BD_ADDR.
- [ ] **RSSI.** Anything worse than about −80 dBm will not hold a session.
- [ ] Does `info` list `Serial Port (00001101-0000-1000-8000-00805f9b34fb)` in
      its UUIDs? If not, `sdptool browse <BD_ADDR>` forces SDP — and unlike
      discovery, that is not blocked by Home Assistant's session. libairmini's
      notes put SPP on channel 5.
- [ ] Does it report a **Class**? A Classic device has one; a pure BLE device
      does not, so this doubles as confirmation of the transport.

If it does not appear, or RSSI is poor, the add-on cannot own the link and the
architecture changes: either a bridge in the bedroom, or the phone app becomes
the only consumer.

## 2. Can a container open an RFCOMM socket?

**Answered: yes** — by prior art rather than by experiment.
[`scyto/ha-bluetooth-audio-manager`](https://github.com/scyto/ha-bluetooth-audio-manager)
is a Home Assistant add-on doing Bluetooth Classic A2DP, and its AppArmor
profile settles it:

```
  network bluetooth,

  # Block raw HCI character-device access -- all Bluetooth configuration
  # MUST go through BlueZ D-Bus to avoid interfering with HA's passive
  # BLE scanning and other integrations.  Note: hcitool rssi uses
  # AF_BLUETOOTH sockets (permitted by "network bluetooth" above),
  # NOT /dev/hci* character devices, so this deny does not block it.
  deny /dev/hci* rw,
```

What it needs, and nothing more: `host_dbus: true`, `privileged: [NET_ADMIN,
NET_RAW]`, and `apparmor: true` with a custom profile carrying `network
bluetooth,`. No `udev`, no `devices`, no `host_network`. It runs Python with
`dbus-next`, and ships a BlueZ pairing agent — which airsupply needs too, for
the link-layer bond that precedes libairmini's SRP-6a.

Its design rule is worth adopting wholesale: **never touch `/dev/hci*`, always
go through BlueZ D-Bus**, precisely so as not to disturb Home Assistant's
passive BLE scanning.

That also suggests a better transport than a raw socket.
`ProfileManager1.RegisterProfile` with the SPP UUID returns a connected **file
descriptor** through `Profile1.NewConnection`, and `dbus-next` can receive Unix
FDs. That fd is exactly the byte stream libairmini wants, obtained the
BlueZ-blessed way.

- [ ] Confirm `RegisterProfile` + `NewConnection` yields a usable fd for the
      AirMini specifically, rather than only for well-known SPP peers.

**A correction worth keeping:** being unable to `scan off` while Home Assistant
is running is *not* evidence that the adapter is unusable. BlueZ discovery
sessions are per-D-Bus-client, and one client cannot stop another's. Connecting
to a known device is unaffected — this add-on streams audio from Classic devices
while HA's passive BLE scan runs continuously.

## 3. Does pairing work, and does the PIN flow match?

Two separate pairings are involved and it is easy to conflate them: the
Bluetooth Classic bond at the link layer, and libairmini's SRP-6a pairing at the
application layer using the PIN on the device.

- [ ] Does the machine require a link-layer bond before it will accept RFCOMM?
- [ ] Does SRP-6a pairing complete, and is a `masterPairKey` returned?
- [ ] Does a later no-PIN reconnect work with that key?

## 4. Does exactly one connection work at a time?

- [ ] With the socket held open, can ResMed's own app still connect?
- [ ] Can a second RFCOMM connection be opened from another host?

The answer decides whether the add-on and the phone app can coexist, or whether
one process must own the machine and serve everything else.

## 5. Do the reads reproduce?

libairmini reports these verified. Confirming them here proves the whole stack —
transport, framing, crypto backend, session — before anything is written.

- [ ] `GetVersion`, `GetDateTime`
- [ ] `GetLoggedData`, `GetHistory` for a real night
- [ ] `StartStream` — 25 Hz flow and pressure

## 6. The `Set` path

Only after 1–5. This is the part libairmini left unverified, and the reason for
this repo.

- [ ] Read a setting, write it back unchanged, read it again — does it round-trip?
- [ ] Change one harmless setting and confirm it on the device's own display.
- [ ] Only then, the settings that affect therapy.

> Write carefully. This is a medical device that someone sleeps attached to.
> Verify a setting on the machine's own screen before trusting a write, and do
> not experiment on a machine you are going to use that night.
