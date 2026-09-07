"""airsupply add-on entry point.

Still a diagnostic, not a monitor. It serves a page through Home Assistant's
ingress that walks through the experiments in docs/verify.md which can be
run from inside the container, in the order they build on each other:

    1. Can BlueZ see the machine, at what signal strength, and does it
       advertise a serial service?
    3. Does the Bluetooth bond form, does the serial channel open, and does
       the handshake complete?
    5. Do the reads reproduce -- version, clock, settings, run meters?

Experiment 6, the write path, is not here and is not reachable from here. The
AirMini is a medical device someone sleeps attached to, and no write happens
until the reads are proven against real hardware.

Configuration is environment: AIRSUPPLY_LOG_LEVEL and AIRSUPPLY_PORT. Which
machine, and the key that pairs with it, live in /data and are managed from
the page.
"""

import asyncio
import logging
import os
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus

from . import web
from .controller import Controller

log = logging.getLogger("airsupply")

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


async def run():
    port = int(os.environ.get("AIRSUPPLY_PORT", "8099"))
    version = os.environ.get("AIRSUPPLY_VERSION", "dev").lstrip("v")

    # negotiate_unix_fd is not optional: without it BlueZ's NewConnection call
    # carries a file descriptor the bus is not prepared to receive, and the
    # message is rejected rather than delivered.
    bus = await MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=True).connect()
    log.info("Connected to the system bus.")

    controller = Controller(bus, version)
    await controller.start()
    await web.serve(web.make_app(controller), port)

    # Run until the bus goes away, which is the one thing we cannot recover
    # from in place. Exiting lets the Supervisor restart us against a fresh
    # BlueZ.
    await bus.wait_for_disconnect()
    log.error("Lost the system bus.")
    return 1


def main():
    level = LEVELS.get(os.environ.get("AIRSUPPLY_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s", stream=sys.stdout)
    # aiohttp's access log is noise under polling; errors still surface.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001
        log.exception("airsupply failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
