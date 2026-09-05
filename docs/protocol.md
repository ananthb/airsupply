# Protocol

This document records what airsupply relies on, and how confident each part is.
The protocol work itself is [libairmini][lib]'s; this is the summary needed to
read the code here, plus the things that are specific to running it on Linux and
on a phone.

[lib]: https://github.com/psychoticbeef/libairmini

## Transport — Bluetooth Classic RFCOMM/SPP

Not BLE. Evidence, from decompiling `com.resmed.airmini` 1.8.0.0.331:

- The app calls `listenUsingRfcommWithServiceRecord(..., UUID
  00001101-0000-1000-8000-00805F9B34FB)` — the well-known SPP UUID.
- There are no uses of `BluetoothGatt` or `writeCharacteristic` anywhere in it.
- On iOS the device is an MFi/iAP2 accessory on ResMed's registered protocol
  `com.resmed.rpc`, reached through `ExternalAccessory`.

So the transport is a plain bidirectional byte stream, and each host opens it
its own way:

| host | how |
|---|---|
| Linux (the add-on) | `socket.socket(AF_BLUETOOTH, SOCK_STREAM, BTPROTO_RFCOMM)` — in Python's stdlib, no third-party library needed |
| Android | `BluetoothDevice.createRfcommSocketToServiceRecord(...)`, a Java API, so it lives in a Kotlin platform channel |
| iOS | `EASession` streams via `ExternalAccessory`, in a Swift platform channel |

libairmini never touches any of them: the host supplies a `send` callback and
feeds received bytes back in with `airmini_feed()`.

## Framing, pairing, session

Summarised from libairmini's `docs/PROTOCOL.md`, which is the authority. All of
this is marked verified against hardware there.

- **NCP frames**, little-endian, length-based, with `0xCAFEBABE` used only to
  resynchronise after an error. Header is `vcid | length | dataCrc | headerCrc`.
- **CRC-32** with polynomial `0xEDB88320`, init `0xFFFFFFFF`, and **no final
  XOR** — the known-answer for `"123456789"` is `0x340BC6D9`, not the usual
  `0xCBF43926`. Worth restating because every off-the-shelf CRC-32 gets this
  wrong by default.
- **Two virtual channels**: plaintext for the handshake (`0x0393` out, `0x0392`
  in) and secure for RPC (`0x0397` out, `0x0396` in). Sending ciphertext on the
  plaintext channel returns a parse error.
- **SRP-6a** pairing using the PIN shown on the device, after which a 64-hex
  `masterPairKey` is stored and used for hands-free reconnection.
- **AES-256-CBC** session carrying `[u16 len][JSON]` JSON-RPC datagrams.

## Crypto backend

libairmini links no crypto of its own. The host fills a five-function vtable:

```c
random_bytes  aes256_cbc_encrypt  aes256_cbc_decrypt  sha256  hmac_sha256
```

That is small enough that no host here needs OpenSSL, which matters most on
mobile, where cross-compiling it is the worst part of the build:

| host | backend |
|---|---|
| add-on | whatever the container already has; OpenSSL is fine on Linux |
| iOS | CommonCrypto, as AirMiniKit already does |
| Android | mbedTLS, or a vendored AES/SHA-256/HMAC — both cross-compile easily |

## What is unverified

These are the open questions, and the reason `docs/verify.md` exists.

1. **The `Set` / config write path.** libairmini's read path is verified live;
   the setters are provisional, use a nested `therapyProfiles` shape, and have
   never been confirmed on a device. This is the part airsupply exists to
   settle, and the fix belongs upstream in C under BSD-2.
2. **Whether the machine accepts more than one RFCOMM connection.** Almost
   certainly not. If it does not, then exactly one process may hold the link,
   and the add-on and the phone app cannot both be connected — nor can ResMed's
   own app. That would force a design where one owner talks to the machine and
   everything else talks to the owner.
3. **Whether a Home Assistant add-on container can open `AF_BLUETOOTH` at
   all.** The Supervisor has no `bluetooth` add-on option — only `host_dbus` and
   `udev` — so BlueZ is reachable over D-Bus, but the RFCOMM socket itself is
   created inside the container and must also get past the add-on's AppArmor
   profile. Unproven.
