# Verification plan

Experiments against a real machine, in dependency order. Nothing here should be
skipped on the grounds that it is obviously fine — the whole project already
changed shape once on a transport assumption that turned out to be wrong.

Record results by editing this file. A failed experiment is a result.

---

## 1. Can the Home Assistant host see the machine?

**Blocks everything.** Bluetooth Classic on a PCB antenna is good for roughly
ten metres line-of-sight, and the machine is on a nightstand.

Put the AirMini into pairing mode first — it does not stay discoverable — then
from the *Advanced SSH & Web Terminal* add-on with protection mode **off**, so
you are on the host rather than in the add-on container:

```sh
bluetoothctl
  power on
  scan on
```

Record:

- [ ] Does an AirMini appear? Its name and BD_ADDR.
- [ ] RSSI. Anything worse than about −80 dBm will not hold a session.
- [ ] Does `sdptool browse <BD_ADDR>` list a Serial Port service, and on which
      channel? libairmini's notes say channel 5.

If it does not appear, or RSSI is poor, the add-on cannot own the link and the
architecture changes to a bridge in the bedroom.

## 2. Can a container open an RFCOMM socket?

The Supervisor exposes no `bluetooth` add-on option — only `host_dbus` and
`udev` — so this is genuinely unknown rather than merely undocumented.

From inside a container on the host, with `host_dbus: true` and `udev: true`:

```python
import socket
s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
s.connect(("XX:XX:XX:XX:XX:XX", 5))
```

- [ ] Does creating the socket succeed, or does seccomp/AppArmor refuse it?
- [ ] Does `connect` succeed once the device is bonded?
- [ ] If AppArmor refuses it, does a custom `apparmor.txt` for the add-on fix it?

Failing this does not kill the project — it means the RFCOMM owner runs outside
the add-on and the add-on becomes a client of it.

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
