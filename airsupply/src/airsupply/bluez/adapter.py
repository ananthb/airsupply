"""The adapter and its devices, as plain dicts for the page to render."""

import logging

from dbus_next import Variant

from . import constants as c
from .survey import looks_like_airmini, managed_objects

log = logging.getLogger(__name__)


async def _interface(bus, path, name):
    introspection = await bus.introspect(c.BLUEZ, path)
    return bus.get_proxy_object(c.BLUEZ, path, introspection).get_interface(name)


def _unwrap(props):
    return {k: v.value for k, v in props.items()}


def _device(path, props):
    uuids = [u.lower() for u in props.get("UUIDs", [])]
    return {
        "path": path,
        "address": str(props.get("Address", "")).upper(),
        "name": props.get("Name") or props.get("Alias") or "",
        "rssi": props.get("RSSI"),
        # A Classic device has a Class; a pure BLE device does not.
        "classic": "Class" in props,
        "class": hex(props["Class"]) if "Class" in props else None,
        "spp": c.SPP_UUID in uuids,
        "paired": bool(props.get("Paired")),
        "connected": bool(props.get("Connected")),
        "trusted": bool(props.get("Trusted")),
        "candidate": looks_like_airmini(props),
    }


async def snapshot(bus):
    """The first adapter and every device BlueZ knows, in one round trip."""
    objects = await managed_objects(bus)
    adapter = None
    devices = []
    for path, ifaces in objects.items():
        if c.ADAPTER in ifaces and adapter is None:
            a = _unwrap(ifaces[c.ADAPTER])
            adapter = {
                "path": path,
                "address": a.get("Address"),
                "name": a.get("Name", ""),
                "powered": bool(a.get("Powered")),
                "discovering": bool(a.get("Discovering")),
            }
        if c.DEVICE in ifaces:
            devices.append(_device(path, _unwrap(ifaces[c.DEVICE])))
    # Candidates first, then by signal, then by name -- the machine should be
    # the first row whenever it is there at all.
    devices.sort(key=lambda d: (not d["candidate"], -(d["rssi"] or -999), d["name"]))
    return adapter, devices


async def start_discovery(bus, adapter_path):
    """Our own discovery session, alongside Home Assistant's BLE one.

    Sessions are per client and the adapter takes the union of every
    client's filter, so asking for BR/EDR here adds Classic inquiry to
    whatever is already running and stops nothing.
    """
    adapter = await _interface(bus, adapter_path, c.ADAPTER)
    await adapter.call_set_discovery_filter({"Transport": Variant("s", "bredr")})
    await adapter.call_start_discovery()


async def stop_discovery(bus, adapter_path):
    adapter = await _interface(bus, adapter_path, c.ADAPTER)
    await adapter.call_stop_discovery()


async def pair(bus, device_path):
    """Create the link-layer bond. BlueZ will ask our agent the questions."""
    device = await _interface(bus, device_path, c.DEVICE)
    await device.call_pair()
    # Trusted lets later connections through without an authorization prompt.
    await device.set_trusted(True)


async def disconnect_profile(bus, device_path):
    device = await _interface(bus, device_path, c.DEVICE)
    await device.call_disconnect_profile(c.SPP_UUID)


async def remove(bus, adapter_path, device_path):
    """Forget the device: drops the bond and everything BlueZ cached."""
    adapter = await _interface(bus, adapter_path, c.ADAPTER)
    await adapter.call_remove_device(device_path)
