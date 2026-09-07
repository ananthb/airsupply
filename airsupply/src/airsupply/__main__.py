"""airsupply add-on entry point.

Still a diagnostic, not a monitor. It runs the experiments in docs/verify.md
that can be run from inside the container, in the order they build on each
other, and stops at whichever one it cannot get past:

    1. Can BlueZ see the machine, at what signal strength, and does it
       advertise a serial service?               -- always
    3. Does the serial channel open, and does the handshake complete?
                                                 -- connect: true
    5. Do the reads reproduce -- version, clock, settings, run meters?
                                                 -- read: true

Experiment 6, the write path, is not here and is not reachable from here. The
AirMini is a medical device someone sleeps attached to, and no write happens
until the reads are proven against real hardware.
"""

import asyncio
import json
import logging
import os
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus

from . import store
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


def _dump(title, value):
    """Log a JSON result so it can be lifted straight out of the add-on log."""
    log.info("--- %s ---", title)
    if value is None:
        log.info("(empty)")
        return
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    for line in text.splitlines():
        log.info("%s", line)


async def handshake(session, address, pin):
    """Get to SESSION_OPEN, preferring the stored key over the PIN.

    Two routes, and which one runs is the answer to experiment 3. The stored
    key is tried first because it is the one that has to work unattended --
    pairing needs someone standing at the machine, and a monitor that needs
    that every night is not a monitor.
    """
    stored = store.master_pair_key(address)

    if stored:
        log.info("Reconnecting with the stored pairing key (no PIN needed).")
        try:
            await session.open_session(stored)
            log.info("Session open. State: %s", session.state)
            return True
        except Exception as err:
            log.warning("Reconnect with the stored key failed: %s", err)
            if not pin:
                log.error("No PIN configured, so there is nothing else to try.")
                log.error("If the machine was factory reset, clear its stored key")
                log.error("by deleting /data/airsupply.json and set a PIN.")
                return False
            log.info("Falling back to PIN pairing.")

    if not pin:
        log.error("No stored pairing key and no PIN configured.")
        log.error("Put the machine in pairing mode, read the number off it,")
        log.error("and set it as the `pin` option. It is needed exactly once.")
        return False

    log.info("Pairing with the PIN via SRP-6a.")
    result = await session.pair(pin)
    log.info("Paired. State: %s", session.state)

    key = result.get("masterPairKey") if isinstance(result, dict) else None
    if key:
        store.remember_master_pair_key(address, key)
        log.info("You can clear the `pin` option now.")
    else:
        log.warning("No masterPairKey in the pairing result; the PIN will be")
        log.warning("needed again next time. Result keys: %s",
                    sorted(result) if isinstance(result, dict) else type(result).__name__)
    return True


async def read_all(session):
    """Experiment 5: the reads libairmini's author verified, reproduced here.

    Each is reported separately rather than aborting the lot on the first
    failure -- knowing that three of four work is a more useful result than
    knowing the first one didn't.
    """
    reads = (
        ("GetVersion", session.get_version),
        ("GetDateTime", session.get_datetime),
        ("Settings", session.get_settings),
        ("MachineMetrics", session.get_metrics),
    )
    failures = 0
    for title, call in reads:
        try:
            _dump(title, await asyncio.wait_for(call(), timeout=30))
        except asyncio.TimeoutError:
            log.error("%s timed out after 30s.", title)
            failures += 1
        except Exception as err:
            log.error("%s failed: %s", title, err)
            failures += 1
    return failures


async def run():
    address = os.environ.get("AIRSUPPLY_ADDRESS", "").strip()
    pin = os.environ.get("AIRSUPPLY_PIN", "").strip()
    want_read = _env_bool("AIRSUPPLY_READ")
    # Reading implies connecting; there is no way to read without a channel.
    want_connect = _env_bool("AIRSUPPLY_CONNECT") or want_read

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
    log.info("Serial channel to %s is open.", wanted)

    if not want_read:
        log.info("read is off -- stopping at the open channel.")
        spp.close(fd)
        return 0

    # Imported here rather than at module scope so that a survey still runs on
    # an image where libairmini failed to build.
    from .airmini import Session

    session = Session(fd)
    try:
        session.open()
        if not await asyncio.wait_for(handshake(session, wanted, pin), timeout=60):
            return 1
        failures = await read_all(session)
    except asyncio.TimeoutError:
        log.error("The handshake did not complete within 60s.")
        return 1
    except Exception:
        log.exception("The session failed")
        return 1
    finally:
        session.close()
        spp.close(fd)

    if failures:
        log.warning("%d of 4 reads failed. Please open an issue with this log.", failures)
        return 1

    log.info("")
    log.info("All four reads returned. That is experiment 5 answered, and the")
    log.info("read path proven end to end on your machine.")
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
