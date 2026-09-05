"""airsupply add-on entry point.

Right now this is a diagnostic, not a monitor. It answers the questions in
docs/verify.md that can be answered from inside the container -- can BlueZ see
the machine, at what signal strength, does it advertise a serial service, and
does that serial channel actually open -- and it stops there. Nothing is read
from the machine, and nothing is written to it.

That order is deliberate. The AirMini is a medical device someone sleeps
attached to, and every write path stays unimplemented until the read path is
proven against real hardware.
"""

import asyncio
import logging
import os
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus

from .bluez import spp, survey

log = logging.getLogger("airsupply")

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _env_bool(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


async def run():
    address = os.environ.get("AIRSUPPLY_ADDRESS", "").strip()
    want_connect = _env_bool("AIRSUPPLY_CONNECT")

    # negotiate_unix_fd is not optional: without it BlueZ's NewConnection call
    # carries a file descriptor the bus is not prepared to receive, and the
    # message is rejected rather than delivered.
    bus = await MessageBus(
        bus_type=BusType.SYSTEM, negotiate_unix_fd=True
    ).connect()
    log.info("Connected to the system bus.")

    candidates = await survey.survey(bus)

    if not want_connect:
        log.info("connect is off -- survey only. Set it once the above looks right.")
        return 0

    if not address:
        log.error("connect is on but no address is configured. Nothing to connect to.")
        return 1

    wanted = address.strip().upper()
    match = next(
        (p for p, d in candidates if str(d.get("Address", "")).upper() == wanted),
        None,
    )
    if match is None:
        log.error("%s is not among the devices BlueZ knows about.", wanted)
        return 1

    profile = await spp.register(bus)
    fd = await spp.connect(bus, match, profile)
    if fd is None:
        return 1

    log.info("")
    log.info("Serial channel to %s is open.", wanted)
    log.info("This is as far as airsupply goes today: the transport works, and")
    log.info("the protocol on top of it is libairmini's, not yet wired up.")
    spp.close(fd)
    return 0


def main():
    level = LEVELS.get(os.environ.get("AIRSUPPLY_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s", stream=sys.stdout)

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    except Exception:
        log.exception("airsupply failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
