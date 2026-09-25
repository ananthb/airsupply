"""Open the AirMini's RFCOMM channel the way BlueZ intends.

Rather than opening an AF_BLUETOOTH socket ourselves, we register a Profile1
implementation for the Serial Port UUID and let BlueZ hand us a connected file
descriptor through NewConnection. Two reasons:

  * It respects the rule this add-on inherits from
    scyto/ha-bluetooth-audio-manager -- never touch /dev/hci*, always go through
    BlueZ -- so Home Assistant's passive BLE scan is undisturbed.
  * The fd is exactly the bidirectional byte stream libairmini wants for its
    `send` callback and `airmini_feed`, with no framing of our own in between.

Receiving a file descriptor over D-Bus requires the bus to have negotiated fd
passing; see main(). Without it dbus-next will refuse the message rather than
hand back a broken fd.
"""

import asyncio
import logging
import os

from dbus_next import Variant
from dbus_next.service import ServiceInterface, method

from . import constants as c

log = logging.getLogger(__name__)


class SerialProfile(ServiceInterface):
    """Where a connected serial channel arrives.

    One slot per machine rather than one for the profile. With a connection
    held open to each machine, two of them can be connecting at the same
    moment, and a single slot would hand the second machine's channel to
    whoever asked first.
    """

    def __init__(self):
        super().__init__(c.PROFILE)
        self._waiting = {}
        self._hangup = {}

    def expect(self, device_path):
        """Arm for a connection from one machine, and return its future."""
        pending = self._waiting.get(device_path)
        if pending is not None and not pending.done():
            pending.cancel()
        future = asyncio.get_running_loop().create_future()
        self._waiting[device_path] = future
        return future

    def on_hangup(self, device_path, callback):
        self._hangup[device_path] = callback

    def forget(self, device_path):
        self._waiting.pop(device_path, None)
        self._hangup.pop(device_path, None)

    @method()
    def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}"):  # noqa: N802
        props = {k: v.value for k, v in fd_properties.items()}
        future = self._waiting.pop(device, None)
        if future is not None and not future.done():
            log.debug("Channel open for %s on fd %s, %s", device, fd, props)
            future.set_result((device, fd, props))
        else:
            # Nobody is waiting; do not leak the descriptor.
            close(fd)

    @method()
    def RequestDisconnection(self, device: "o"):  # noqa: N802
        log.info("The machine asked to disconnect.")
        callback = self._hangup.get(device)
        if callback is not None:
            callback()

    @method()
    def Release(self):  # noqa: N802
        log.info("Serial profile released.")


async def register(bus):
    """Export our Profile1 and register it for the SPP UUID."""
    profile = SerialProfile()
    bus.export(c.PROFILE_PATH, profile)

    introspection = await bus.introspect(c.BLUEZ, c.BLUEZ_ROOT)
    obj = bus.get_proxy_object(c.BLUEZ, c.BLUEZ_ROOT, introspection)
    manager = obj.get_interface(c.PROFILE_MANAGER)

    # Role "client": we are connecting out to the machine, not listening.
    # Channel is left to SDP; SPP_CHANNEL is recorded in constants as the value
    # to expect, not to force.
    await manager.call_register_profile(
        c.PROFILE_PATH,
        c.SPP_UUID,
        {
            "Name": Variant("s", "airsupply"),
            "Role": Variant("s", "client"),
            "AutoConnect": Variant("b", False),
            "RequireAuthentication": Variant("b", True),
            "RequireAuthorization": Variant("b", False),
        },
    )
    log.debug("Registered the serial profile at %s", c.PROFILE_PATH)
    return profile


class NotReachable(Exception):
    """The serial channel did not open, and we know roughly why.

    BlueZ says things like `br-connection-timeout`, which is accurate and
    tells somebody looking at their own bedroom nothing. The reason is known
    at the point it happens and thrown away everywhere else, so it is turned
    into a sentence here instead.
    """


def explain(err):
    text = str(getattr(err, "text", "") or err).lower().replace("-", " ")
    if "timeout" in text:
        return (
            "No answer. Machine off or out of range."
        )
    if "profile unavailable" in text:
        return (
            "No serial port on the machine."
        )
    if "already connected" in text:
        return "Machine in use elsewhere."
    if "in progress" in text:
        return "Already connecting."
    if "refused" in text or "not available" in text:
        return "Connection refused."
    return f"Channel did not open: {getattr(err, 'text', None) or err}"


async def connect(bus, device_path, profile, timeout=30.0):
    """Ask BlueZ to bring up the serial profile, and wait for the fd.

    Returns the file descriptor; the caller owns it. Raises NotReachable with
    something worth reading when it does not open.
    """
    introspection = await bus.introspect(c.BLUEZ, device_path)
    obj = bus.get_proxy_object(c.BLUEZ, device_path, introspection)
    device = obj.get_interface(c.DEVICE)

    if not await device.get_paired():
        raise NotReachable(
            "Not paired."
        )

    arrived = profile.expect(device_path)
    log.debug("Opening the serial channel to %s", device_path)
    try:
        await device.call_connect_profile(c.SPP_UUID)
    except Exception as err:  # noqa: BLE001 -- BlueZ errors are opaque strings
        log.debug("ConnectProfile failed: %s", err)
        raise NotReachable(explain(err)) from err

    try:
        _, fd, _ = await asyncio.wait_for(arrived, timeout)
    except asyncio.TimeoutError as err:
        raise NotReachable(
            "Connected, but the channel never opened."
        ) from err

    return fd


def close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
