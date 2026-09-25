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
    """BlueZ calls into this once a serial connection exists."""

    def __init__(self):
        super().__init__(c.PROFILE)
        self.connected = None
        self.reset()

    def reset(self):
        """Arm for the next connection. One future per ConnectProfile."""
        self.connected = asyncio.get_running_loop().create_future()

    @method()
    def NewConnection(self, device: "o", fd: "h", fd_properties: "a{sv}"):  # noqa: N802
        props = {k: v.value for k, v in fd_properties.items()}
        log.info("NewConnection from %s", device)
        log.info("  fd %s, properties %s", fd, props)
        if not self.connected.done():
            self.connected.set_result((device, fd, props))
        else:
            # Nobody is waiting; do not leak the descriptor.
            close(fd)

    @method()
    def RequestDisconnection(self, device: "o"):  # noqa: N802
        log.info("RequestDisconnection from %s", device)

    @method()
    def Release(self):  # noqa: N802
        log.info("Profile released by BlueZ.")


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
    log.info("Registered SPP profile at %s", c.PROFILE_PATH)
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
            "The machine did not answer. It is switched off, out of range, or "
            "its Bluetooth is asleep -- an AirMini only listens for a while "
            "after it is powered on."
        )
    if "profile unavailable" in text:
        return (
            "The machine answered but offered no serial port. If it has been "
            "factory reset, forget it here and set it up again."
        )
    if "already connected" in text:
        return "Something else is connected to the machine; only one thing can be."
    if "in progress" in text:
        return "A connection to the machine is already being made."
    if "refused" in text or "not available" in text:
        return "The machine refused the connection."
    return f"The serial channel did not open: {getattr(err, 'text', None) or err}"


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
            "The Bluetooth pairing is missing. It has to exist before the serial "
            "channel will open, and it is separate from the machine's own PIN."
        )

    profile.reset()
    log.info("Connecting serial profile...")
    try:
        await device.call_connect_profile(c.SPP_UUID)
    except Exception as err:  # noqa: BLE001 -- BlueZ errors are opaque strings
        log.debug("ConnectProfile failed: %s", err)
        raise NotReachable(explain(err)) from err

    try:
        _, fd, _ = await asyncio.wait_for(profile.connected, timeout)
    except asyncio.TimeoutError as err:
        raise NotReachable(
            "Bluetooth connected but the machine never opened the channel. "
            "Something is wrong inside the add-on rather than in the bedroom; "
            "set log_level to debug and send the log."
        ) from err

    log.info("Serial channel open on fd %s.", fd)
    return fd


def close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
