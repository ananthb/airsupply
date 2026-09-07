# airsupply

**This add-on does not monitor anything yet.** It is a diagnostic. It reports
what BlueZ can see of your ResMed AirMini, opens the serial channel to it, and
reads four things back: firmware version, the machine's clock, your therapy
settings, and the run meters. It writes nothing.

## Why a diagnostic first

The AirMini speaks Bluetooth Classic RFCOMM, not BLE, so none of the usual Home
Assistant Bluetooth machinery reaches it. Several things had to be true before
writing a monitor, and only one of them was answerable from a desk:

1. The Home Assistant host has to be within about ten metres of the machine.
2. An add-on container has to be allowed to use Bluetooth Classic at all.
3. The serial channel has to open and the handshake has to complete.
4. The reads have to reproduce on your machine, not just the one they were
   recovered from.

Number 2 is settled — see `docs/verify.md` in the repository. This add-on
answers 1, 3 and 4 on your own hardware, in that order, and stops at whichever
one it cannot get past.

## Install

Add `https://github.com/ananthb/airsupply` as an add-on repository, then
install **airsupply**. It is built locally; there is no image to pull. The
build compiles [libairmini][] from the commit pinned in
`airsupply/libairmini.pin`, so the first install takes a few minutes.

[libairmini]: https://github.com/psychoticbeef/libairmini

## Step 1 — survey

Start with the defaults. `address` empty and everything off means survey only:

```yaml
log_level: info
address: ""
connect: false
read: false
pin: ""
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
be the thing that talks to the machine. That is a real answer — it means the
phone app has to be the one that reads, and Home Assistant gets the data from
there instead.

## Step 2 — open the channel

The Bluetooth Classic bond has to exist first, and it is **not** the same as the
PIN pairing the AirMini's own app does. From the host, once:

```sh
bluetoothctl
  pair <address>
```

Then set `address` and turn `connect` on. Success is `Serial channel open on
fd N`.

## Step 3 — pair and read

Turn `read` on and put the number the machine shows in pairing mode into `pin`:

```yaml
address: "00:11:22:33:44:55"
read: true
pin: "123456"
```

The PIN is needed exactly once. What it buys is a `masterPairKey`, which the
add-on stores in `/data/airsupply.json` and uses for every connection after
that — no PIN, nobody standing at the machine. **Clear the `pin` option once
pairing has succeeded**; the `password` field type keeps it out of the log, not
out of a Home Assistant backup.

A good run ends with four JSON blocks in the log and:

```
All four reads returned. That is experiment 5 answered, and the
read path proven end to end on your machine.
```

Please send that log — with the serial numbers taken out — to the repository.
It is the evidence the rest of the project is waiting on.

### If it goes wrong

| symptom | likely cause |
|---|---|
| `Device is not paired` | the Classic bond from step 2 is missing |
| `ConnectProfile failed` | the machine is asleep, out of range, or already talking to the phone app |
| `NewConnection never arrived` | BlueZ accepted the connect but never handed over the fd — an AppArmor denial is the usual reason; check the Supervisor log |
| `Reconnect with the stored key failed` | the machine was reset; delete `/data/airsupply.json` and pair again |
| reads time out | the session opened but the machine is not answering — turn on `log_level: debug` and capture the byte counts |

## What it deliberately does not do

Write. The `Set` path in the underlying protocol library is unverified upstream,
this is a medical device, and the read path has to be proven first. The add-on
declares no `airmini_set_*` prototypes and refuses any JSON-RPC method that is
not a read, so getting to a write means editing `READ_METHODS` on purpose. See
the repository's `docs/verify.md` for the order the remaining experiments go in.
