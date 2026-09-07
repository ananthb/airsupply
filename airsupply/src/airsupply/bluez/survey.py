"""Report what BlueZ can see, which is experiment 1 of docs/verify.md.

This goes through BlueZ's D-Bus API rather than `bluetoothctl scan`, which
matters: discovery sessions are per-D-Bus-client, so Home Assistant's passive
BLE scan cannot be stopped or filtered by us, and its output drowns everything.
Reading the object tree sidesteps that entirely -- every device BlueZ knows
about is already here, with the properties we need.
"""

import logging

from . import constants as c

log = logging.getLogger(__name__)


def _unwrap(props):
    """dbus-next hands back Variants; we want plain Python."""
    return {k: v.value for k, v in props.items()}


async def managed_objects(bus):
    introspection = await bus.introspect(c.BLUEZ, c.ROOT)
    obj = bus.get_proxy_object(c.BLUEZ, c.ROOT, introspection)
    manager = obj.get_interface(c.OBJECT_MANAGER)
    return await manager.call_get_managed_objects()


def looks_like_airmini(device):
    name = f"{device.get('Name', '')} {device.get('Alias', '')}".lower()
    if any(hint in name for hint in c.NAME_HINTS):
        return True
    uuids = [u.lower() for u in device.get("UUIDs", [])]
    return c.SPP_UUID in uuids


async def survey(bus):
    """Log every adapter and device, flagging anything AirMini-shaped.

    Returns the list of candidate devices as (path, properties) pairs.
    """
    objects = await managed_objects(bus)

    adapters = {
        path: _unwrap(ifaces[c.ADAPTER])
        for path, ifaces in objects.items()
        if c.ADAPTER in ifaces
    }
    devices = {
        path: _unwrap(ifaces[c.DEVICE])
        for path, ifaces in objects.items()
        if c.DEVICE in ifaces
    }

    if not adapters:
        log.error("No Bluetooth adapter visible over D-Bus. Nothing can work.")
        return []

    log.info("Adapters:")
    for path, a in adapters.items():
        log.info(
            "  %s  %s  powered=%s discovering=%s  %s",
            path.rsplit("/", 1)[-1],
            a.get("Address", "??"),
            a.get("Powered"),
            a.get("Discovering"),
            a.get("Name", ""),
        )
    if any(a.get("Discovering") for a in adapters.values()):
        log.info(
            "  (something is discovering -- almost certainly Home Assistant's "
            "own passive BLE scan. That is fine and does not block us.)"
        )

    log.info("Devices known to BlueZ: %d", len(devices))

    candidates = [(p, d) for p, d in devices.items() if looks_like_airmini(d)]
    if not candidates:
        log.warning(
            "No AirMini-shaped device found. Put the machine into pairing mode "
            "and let BlueZ see it once -- it does not stay discoverable."
        )
        return []

    for path, d in candidates:
        log.info("Candidate: %s", path)
        log.info("  Address   %s", d.get("Address"))
        log.info("  Name      %s / alias %s", d.get("Name"), d.get("Alias"))
        # A Classic device has a Class; a pure BLE device does not. This is the
        # transport confirmation, straight from BlueZ.
        log.info("  Class     %s", hex(d["Class"]) if "Class" in d else "(none -- looks like BLE, not Classic)")
        # RSSI is only populated while the device is in range and recently seen.
        rssi = d.get("RSSI")
        log.info("  RSSI      %s", f"{rssi} dBm" if rssi is not None else "(unknown -- not seen recently)")
        if rssi is not None and rssi < -80:
            log.warning("  RSSI is poor. A session is unlikely to hold at this range.")
        log.info("  Paired    %s   Connected %s", d.get("Paired"), d.get("Connected"))
        uuids = [u.lower() for u in d.get("UUIDs", [])]
        if c.SPP_UUID in uuids:
            log.info("  SPP       yes -- Serial Port service present")
        else:
            log.warning(
                "  SPP       not listed. BlueZ may not have run SDP yet; "
                "pairing, or `sdptool browse %s`, will populate it.",
                d.get("Address"),
            )

    return candidates
