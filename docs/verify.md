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

- [x] Does an AirMini appear? Its name and BD_ADDR. Yes; both are on the page.
- [ ] **RSSI.** Anything worse than about −80 dBm will not hold a session.
- [x] Does `info` list `Serial Port (00001101-0000-1000-8000-00805f9b34fb)` in
      its UUIDs? If not, `sdptool browse <BD_ADDR>` forces SDP — and unlike
      discovery, that is not blocked by Home Assistant's session. libairmini's
      notes put SPP on channel 5.
- [x] Does it report a **Class**? Yes. A Classic device has one and a pure
      BLE device does not, so this doubles as confirmation of the transport;
      the page marks it Classic on the strength of it.

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

- [x] Confirm `RegisterProfile` + `NewConnection` yields a usable fd for the
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

**The add-on runs this one, from its page.** It registers a BlueZ pairing
agent and calls `Device1.Pair` itself, so the link-layer bond no longer needs
a shell on the host; whatever BlueZ asks -- a PIN, a passkey to confirm -- is
shown on the page. Then it asks BlueZ for the SPP file descriptor, runs the
SRP-6a handshake with the PIN typed on the page, stores the resulting
`masterPairKey` under `/data`, and on every later start reconnects with that
key and no PIN. See the add-on's
[DOCS.md](https://github.com/ananthb/hass-addons/blob/main/airsupply/DOCS.md).

**Recorded 2026-09-22 — `Device1.Pair` returns `org.bluez.Error.ConnectionAttemptFailed:
Page Timeout`, repeatedly.** The machine is found and selected (experiment 1
passes), and the bond never gets as far as a question: a page timeout is the
BR/EDR *page* going unanswered, which happens before any pairing method is
negotiated. So this says nothing yet about what the AirMini asks.

Three things have to be true at once for a page to land, and the error names
none of them:

1. **The machine has to be listening.** The AirMini is only connectable while
   it is in pairing mode, and it leaves pairing mode on its own after a short
   while — quite possibly while the 20-second scan is still running.
2. **The inquiry data has to be fresh.** A controller pages using the clock
   offset and page-scan repetition mode it learned during inquiry. BlueZ drops
   `RSSI` once it has not heard from a device lately, and that missing RSSI is
   the visible sign that what it holds is too stale to page with.
3. **The adapter must not be inquiring.** Inquiry and paging are the same
   radio, and it will not page while it is inquiring. Pressing **Bond** as soon
   as the machine appears in the list — the obvious thing to do — pages into
   our own still-running scan.

Home Assistant's passive BLE scan is *not* a fourth: it is LE, it never
inquires, and it keeps the adapter's `Discovering` true without blocking a
page. The note in `survey()` that a busy adapter "does not block us" was right
about that scan and wrong about ours.

The page now covers all three — it stops our scan and lets the radio settle
before paging, re-inquires first when `RSSI` is missing, retries three times,
and reports what to do rather than "Page Timeout". Whether the bond then forms,
and which question the machine asks, is still open below.

**Recorded 2026-09-25 — the bond forms.** With that sequence in place the
link-layer bond went through on the first try against the machine on the
nightstand, so experiment 1 and the first half of this one are answered: the
Home Assistant host is in range, an add-on container can drive BlueZ, and the
bond is reachable from the page.

**Answered 2026-09-25 — the bond forms, the channel opens, and SRP-6a
completes.** In order, from the add-on's own log:

```
Bonding with <BD_ADDR>. Watch the machine and this page for a code.
Bonded with <BD_ADDR>.                      1 second later
Connecting serial profile...
NewConnection from /org/bluez/hci0/dev_<BD_ADDR>
  fd 9, properties {'Version': 256}
Serial channel open on fd 9.
Pairing with the machine via SRP-6a.
Paired. State: session-open
```

- [x] **Which link-layer question does the machine ask?** None. No agent
      method was called at all: the bond went from `Pair` to bonded in about a
      second with nothing shown on the page and nothing typed. So the AirMini
      does just-works SSP, and the agent exists for the case that never came.
      Keep it -- a machine on older firmware may still ask -- but the answer
      here is that the Bluetooth step needs no interaction beyond the button.
- [x] **Does SRP-6a pairing complete, and is a `masterPairKey` returned?**
      Yes to both, and the second half was being thrown away. libairmini
      answers `airmini_pair` with the key as **bare hex, not JSON**, so
      `session._decode` cannot parse it and passes the text through; the
      controller looked only for a `{"masterPairKey": ...}` object, found a
      string, and logged "No masterPairKey in the pairing result". The PIN was
      therefore needed on every connection and the reconnect-on-start path
      could never run. Fixed: either shape is accepted, and the value is
      checked to be hex before it is stored.
- [ ] **Does a later no-PIN reconnect work with that key?** Still open, and
      only testable now that a key is kept. This is the next thing to try:
      restart the add-on and watch for "Reconnecting with the stored pairing
      key" followed by four reads, with nobody at the machine.

Experiment 2 is confirmed live by the same log, having been answered from
prior art: a `NewConnection` carrying a file descriptor arrived, and the
AppArmor profile allowed it.

## 4. Does exactly one connection work at a time?

- [ ] With the socket held open, can ResMed's own app still connect?
- [ ] Can a second RFCOMM connection be opened from another host?

The answer decides whether the add-on and the phone app can coexist, or whether
one process must own the machine and serve everything else.

## 5. Do the reads reproduce?

libairmini reports these verified. Confirming them here proves the whole stack —
transport, framing, crypto backend, session — before anything is written.

**The add-on runs the first four** from its page and reports each separately,
so a partial result is still a result. Paste its log here with serial numbers
removed.

- [x] `GetVersion`
- [x] `GetDateTime`
- [x] `Get` — the therapy settings
- [x] `Get` — the run meters

**Answered 2026-09-25 — all four returned, first attempt.** Identifiers and
the serial number are redacted; shapes and values are otherwise verbatim.

`GetVersion` reports RPC **2.0** and, usefully, lists every method the
firmware implements with its version:

```
ApplyAuthenticatedUpgrade 1.0   GetDateTime 1.0      InitiateUpgrade 1.0
BtDisconnect 1.0                GetHistory 1.0       Set 1.0
CheckUpgradeFile 1.0            GetLoggedData 1.0    StartStream 1.0
DiscardPairKey 1.0              GetPairKey 1.0       SubscribeEvent 1.0
EnterMaskFit 1.0                GetSessionKey 1.0    UpgradeDataBlock 1.0
EnterStandby 1.0                GetVersion 2.0
EnterTherapy 1.0                Get 1.0
EraseData 1.0                   GenerateAuthCode 1.1
```

That answers a question nobody had thought to ask: `GetLoggedData`,
`GetHistory` and `StartStream` are all present on this firmware, so the
night's data and the 25 Hz stream are reachable and only the code to ask for
them is missing. `Set` is there too, which is what experiment 6 is about.

It also carries identification for two subsystems, `FlowGenerator` and
`BluetoothModule`, each with hardware, product and software identifiers. The
flow generator's software block is the interesting one:

```
ApplicationIdentifier       SW03900.01.4.0.3.50927
BootloaderIdentifier        SW03901.00.3.0.0.48255
ConfigurationIdentifier     CF03900.01.03.00.50927
DataModelVersionIdentifier  1.0.0.270
```

`GetDateTime` is one field, UTC: `{"dateTime": "2026-09-25T12:13:15.405Z"}`.

`Get` for the settings returns the live state alongside the stored profiles --
`FGState: Standby`, `ActiveTherapyProfile: AutoSetProfile` -- then
`FeatureProfiles` (auto ramp, comfort, EPR, smart start/stop) and three
`TherapyProfiles`: `AutoSetProfile`, `AutoSetForHerProfile` and `CpapProfile`,
each with its pressures and a `TherapyMode`. Pressures come back as floats in
cmH2O, EPR pressure as an integer, and every switch as the strings `"On"` /
`"Off"` / `"Auto"` rather than as booleans.

`Get` for the run meters returns them as **ISO-8601 durations**, which is
worth knowing before anyone tries to read one as a number:

```
MachineRunMeter                 PT2591392S   719 h 49 min
MotorRunMeter                   PT2591392S
MotorRunSinceLastServiceMeter   PT2591392S
TherapyRunMeter                 PT2589645S   719 h 20 min
LastTherapyUseDateTime          2026-09-25T06:26:51.000Z
LastEraseDataDateTime           null
```

Machine, motor and since-service meters are identical, so this machine has
never been serviced and has never been idle-but-powered for long; therapy run
trails machine run by 1747 s, about 29 minutes across its whole life.

So the stack is proved end to end -- transport, framing, CRC, SRP-6a, the
AES-256-CBC session and the read path -- on hardware other than the one
libairmini was recovered from. The remaining reads and experiment 6 are the
open ones.

- [ ] `GetLoggedData`, `GetHistory` for a real night — not yet in the add-on
- [ ] `StartStream` — 25 Hz flow and pressure — not yet in the add-on

## 6. The `Set` path

Only after 1–5. This is the part libairmini left unverified, and the reason for
this repo.

- [ ] Read a setting, write it back unchanged, read it again — does it round-trip?
- [ ] Change one harmless setting and confirm it on the device's own display.
- [ ] Only then, the settings that affect therapy.

> Write carefully. This is a medical device that someone sleeps attached to.
> Verify a setting on the machine's own screen before trusting a write, and do
> not experiment on a machine you are going to use that night.
