"""The one place state lives, and the operations the page can start.

Every operation runs as a background task; the page polls `state()` and
renders whatever is there. One operation at a time, because they all want
the same serial channel, and a person watching a page is not going to click
two things at once on purpose.

Read-only. Nothing here can reach the machine's Set path; see
airmini/session.py for where that line is drawn.
"""

import asyncio
import collections
import datetime
import json
import logging
import os
import re
import time

from dbus_next import DBusError

from . import hass, mqtt, store
from .bluez import adapter, agent, spp

log = logging.getLogger("airsupply")

SCAN_SECONDS = 20
HANDSHAKE_TIMEOUT = 60
READ_TIMEOUT = 30
LOG_LINES = 300

# The bond, and why it takes this much care. A BR/EDR page reaches a machine
# that is listening for one, using inquiry data that has not gone stale, from
# a controller that is not inquiring itself. Miss any of the three and BlueZ
# answers ConnectionAttemptFailed: Page Timeout -- which says nothing about
# which one it was, so the sequence covers all three before it gives up.
# How often every paired machine is read. The AirMini is a nightstand device
# whose numbers move once a night, so this is about keeping Home Assistant
# current rather than about resolution -- and every read is a window in which
# ResMed's own app cannot have the machine.
READ_EVERY = int(os.environ.get("AIRSUPPLY_READ_EVERY") or 900)

REFRESH_SCAN_SECONDS = 8

RADIO_SETTLE = 2.0
PAIR_ATTEMPTS = 3
PAIR_RETRY_PAUSE = 4.0

PAGE_TIMEOUT_ADVICE = (
    "The machine never answered (Page Timeout). The AirMini only accepts a "
    "connection while it is in pairing mode, and it leaves pairing mode after "
    "a short while -- press its Bluetooth button again and press Bond within "
    "a few seconds of the machine lighting up. If that keeps failing, the host "
    "is out of range: Bluetooth Classic is good for about ten metres."
)


# libairmini answers a completed pairing with
#     {"masterPairKey":"<64 hex>","sessionKey":"<64 hex>"}
# built by snprintf into a `char res[160]` in airmini.c -- 5 bytes short of
# the 165 that string needs. So what arrives is truncated at 159 characters,
# with the closing quote and brace cut off, which is why it will not parse as
# JSON and turns up here as a bare string.
#
# The key itself is unhurt: it ends at character 82, well inside what
# survives. Only the tail of the session key is lost, and nothing here wants
# that -- libairmini has already opened the session with it by this point.
#
# So read the key out of the text rather than requiring the JSON to be whole.
# Reported upstream; this keeps working either way, since a fixed libairmini
# produces a string this still matches.
_PAIR_KEY = re.compile(r"[0-9a-fA-F]{32,128}")
_PAIR_KEY_FIELD = re.compile(r'"masterPairKey"\s*:\s*"([0-9a-fA-F]{32,128})"')

class Busy(Exception):
    pass


class _PageLog(logging.Handler):
    """Mirror the add-on log into a ring buffer the page can show."""

    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.append({
                "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname.lower(),
                "msg": self.format(record),
            })
        except Exception:  # noqa: BLE001 -- a logging handler must not raise
            pass


class Controller:
    def __init__(self, bus, version):
        self.bus = bus
        self.version = version
        self.adapter = None
        self.devices = []
        self.selected = store.selected()
        self.scanning = False
        self.busy = None
        self.error = None
        # One reading per machine, keyed by address. A reading belongs to the
        # machine it came from rather than to the add-on, which is what makes
        # a second machine possible at all.
        self.readings = {}
        self.session_state = None

        self.publisher = mqtt.Publisher(version)
        self.people = []
        self.people_problem = None
        self.log = collections.deque(maxlen=LOG_LINES)
        self.agent = None
        self.agent_ok = False
        self.profile = None
        self._prompt = None
        self._last_refresh = 0.0
        self._task = None
        self._scan_task = None
        self._scan_stop = None
        self._schedule_task = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self):
        handler = _PageLog(self.log)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)

        self.agent = agent.PairingAgent(self._on_prompt)
        try:
            await agent.register(self.bus, self.agent)
            self.agent_ok = True
        except Exception as err:  # noqa: BLE001
            log.error("Could not register a pairing agent: %s", err)
            log.error("The Bluetooth bond step will not work from this page.")

        self.profile = await spp.register(self.bus)
        await self.refresh(force=True)

        self.publisher.start()
        await self.refresh_people()
        self._schedule_task = asyncio.create_task(self._schedule())

    async def refresh_people(self):
        """Who Home Assistant knows about, so a machine can belong to one."""
        if not hass.configured():
            self.people_problem = "the add-on has not been granted homeassistant_api"
            return
        try:
            self.people = await hass.people()
            self.people_problem = None
            log.info("Home Assistant knows %d people.", len(self.people))
        except hass.Unavailable as err:
            self.people_problem = str(err)
            log.warning("Could not ask Home Assistant who lives here: %s", err)

    def _paired(self):
        """Machines there is a key for, so they can be read without anybody."""
        return [a for a, m in store.machines().items() if m["paired"]]

    async def _schedule(self):
        """Read every paired machine, in turn, for as long as we are running.

        In turn, because the machine accepts one connection at a time and
        while we hold it ResMed's own app cannot. A machine with no key yet
        is left alone rather than woken up to ask it for one.

        The first pass is immediate: after a restart the numbers in Home
        Assistant are as old as the add-on was down for.
        """
        while True:
            for address in self._paired():
                if self.busy:
                    continue
                try:
                    await self._spawn("read", lambda a=address: self._read(a))
                except Busy:
                    break
            await asyncio.sleep(READ_EVERY)

    # --- state -------------------------------------------------------------

    async def refresh(self, force=False):
        if not force and time.monotonic() - self._last_refresh < 1.0:
            return
        try:
            self.adapter, self.devices = await adapter.snapshot(self.bus)
        except Exception as err:  # noqa: BLE001
            log.error("Could not read BlueZ's object tree: %s", err)
        self._last_refresh = time.monotonic()

    def _selected_device(self):
        for d in self.devices:
            if d["address"] == self.selected:
                return d
        return None

    async def state(self):
        await self.refresh()
        sel = self._selected_device()
        return {
            "version": self.version,
            "busy": self.busy,
            "scanning": self.scanning,
            "agent_ok": self.agent_ok,
            "adapter": self.adapter,
            "devices": self.devices,
            "selected": self.selected,
            "selected_device": sel,
            "bonded": bool(sel and sel["paired"]),
            "has_key": bool(self.selected and store.master_pair_key(self.selected)),
            "prompt": self._prompt.to_json() if self._prompt else None,
            "machines": self.machines(),
            "people": [person.to_json() for person in self.people],
            "people_problem": self.people_problem,
            "reading": self.readings.get(self.selected),
            "publishing": {
                "configured": mqtt.configured(),
                "connected": self.publisher.connected,
                "problem": self.publisher.problem,
            },
            "session_state": self.session_state,
            "error": self.error,
            "log": list(self.log),
        }

    def machines(self):
        """Every machine we have been told about, with whatever is known.

        The store says which machines exist and whose they are; BlueZ says
        whether one is in range and bonded right now. Neither is the whole
        picture on its own -- a machine that is paired and out of range is
        still a machine somebody owns.
        """
        people = {person.id: person for person in self.people}
        out = []
        for address, record in store.machines().items():
            device = next((d for d in self.devices if d["address"] == address), None) or {}
            reading = self.readings.get(address)
            person = people.get(record["person_id"])
            out.append({
                "address": address,
                "name": device.get("name") or record["name"] or "",
                "signal": device.get("rssi"),
                "classic": bool(device.get("classic", True)),
                "serial": bool(device.get("spp")),
                "bonded": bool(device.get("paired")),
                "paired": record["paired"],
                "person": person.to_json() if person else None,
                "person_id": record["person_id"],
                "missing_person": bool(record["person_id"] and not person),
                "last_read": reading["at"] if reading else None,
            })
        return out

    def _on_prompt(self, prompt):
        self._prompt = prompt

    # --- operations --------------------------------------------------------

    def _spawn(self, name, work):
        if self.busy:
            raise Busy(f"{self.busy} is still running")
        self.busy = name
        self.error = None

        async def runner():
            try:
                await work()
            except Exception as err:  # noqa: BLE001
                self.error = str(err) or type(err).__name__
                log.error("%s failed: %s", name, self.error)
                log.debug("traceback", exc_info=True)
            finally:
                self.busy = None
                self._last_refresh = 0.0

        self._task = asyncio.create_task(runner())
        return self._task

    def scan(self, seconds=SCAN_SECONDS):
        if self.scanning:
            return
        if not self.adapter:
            raise RuntimeError("no Bluetooth adapter is visible over D-Bus")
        self.scanning = True
        self._scan_stop = asyncio.Event()
        self._scan_task = asyncio.create_task(self._scan(seconds))

    async def _scan(self, seconds):
        path = self.adapter["path"]
        stop = self._scan_stop
        try:
            await adapter.start_discovery(self.bus, path)
            log.info("Scanning for %ds. Put the AirMini in pairing mode now.", seconds)
            try:
                # Ends early if the bond step asks for the radio back.
                await asyncio.wait_for(stop.wait(), seconds)
            except asyncio.TimeoutError:
                pass
        except Exception as err:  # noqa: BLE001
            self.error = f"scan failed: {err}"
            log.error("Scan failed: %s", err)
        finally:
            try:
                await adapter.stop_discovery(self.bus, path)
            except Exception:  # noqa: BLE001 -- ours may already be over
                pass
            self.scanning = False
            self._last_refresh = 0.0
            log.info("Scan finished.")

    async def _scan_finished(self):
        """Wait for the scan task, if any, to have stopped discovery."""
        task = self._scan_task
        if task is not None:
            await task

    async def stop_scan(self):
        """Cut a running scan short and wait for the inquiry to be over."""
        if self._scan_stop is not None:
            self._scan_stop.set()
        await self._scan_finished()

    def select(self, address):
        """Add a machine, or move the page's attention to one already added.

        A machine BlueZ cannot see is still selectable once it is in the
        store: it is out of range, not gone, and its readings are still
        worth looking at.
        """
        address = (address or "").upper()
        device = next((d for d in self.devices if d["address"] == address), None)
        if device is None and address not in store.machines():
            raise ValueError(f"{address} is not a machine BlueZ knows about")
        store.add(address, (device or {}).get("name", ""))
        store.select(address)
        self.selected = address
        log.info("Looking at %s.", address)

    def assign(self, address, person_id):
        """Say which of Home Assistant's people a machine belongs to."""
        address = (address or self.selected or "").upper()
        if not address:
            raise RuntimeError("no machine chosen")
        person_id = person_id or None
        if person_id and not any(person.id == person_id for person in self.people):
            raise ValueError("Home Assistant does not know that person")
        store.assign(address, person_id)
        self._republish(address)

    def _publish(self, address):
        record = store.machines().get(address) or {}
        person = next((p for p in self.people if p.id == record.get("person_id")), None)
        reading = self.readings.get(address) or {}
        self.publisher.publish(address, record.get("name") or "", person,
                               reading.get("results"), reading.get("at_iso"))

    def _republish(self, address):

        """Say a machine again, after something about it has changed."""
        if address in self.readings:
            self._publish(address)

    def bond(self):
        self._spawn("bond", self._bond)

    async def _bond(self):
        dev = self._selected_device()
        if dev is None:
            raise RuntimeError("no machine selected")
        if dev["paired"]:
            log.info("%s is already bonded.", dev["address"])
            return
        if not self.agent_ok:
            raise RuntimeError("no pairing agent; see the log from start-up")

        dev = await self._ready_to_page(dev)
        log.info("Bonding with %s. Watch the machine and this page for a code.", dev["address"])
        for attempt in range(1, PAIR_ATTEMPTS + 1):
            try:
                await adapter.pair(self.bus, dev["path"])
                break
            except DBusError as err:
                if not adapter.is_page_timeout(err):
                    raise
                log.warning(
                    "Attempt %d of %d: the machine did not answer the page (%s).",
                    attempt, PAIR_ATTEMPTS, err.text or err.type,
                )
                if attempt == PAIR_ATTEMPTS:
                    raise RuntimeError(PAGE_TIMEOUT_ADVICE) from err
                await asyncio.sleep(PAIR_RETRY_PAUSE)
            finally:
                # A failed attempt leaves a stale question on the page.
                self.agent.clear()
        log.info("Bonded with %s.", dev["address"])

    async def _ready_to_page(self, dev):
        """Give the radio its best chance of reaching the machine.

        Paging a Classic device is not connecting to a BLE one. The controller
        pages using the clock offset and page-scan mode it learned from an
        inquiry, and it will not page while it is still inquiring -- so a bond
        started straight off the scan's own device list, which is the obvious
        thing to do, pages into a running inquiry and gets Page Timeout back.
        That is the failure this exists to prevent.

        Home Assistant's passive BLE scan keeps the adapter's `Discovering`
        true whatever we do, so there is nothing to wait for there: it is LE,
        it does not inquire, and it does not block a page. Ours does.
        """
        if self.scanning:
            log.info("Stopping our scan: the adapter will not page while it is inquiring.")
            await self.stop_scan()
            dev = await self._reselect(dev)

        if dev["rssi"] is None:
            log.info(
                "BlueZ has not seen %s recently, so what it holds is too stale to page with. "
                "Inquiring for %ds -- put the AirMini into pairing mode now.",
                dev["address"], REFRESH_SCAN_SECONDS,
            )
            self.scan(REFRESH_SCAN_SECONDS)
            await self._scan_finished()
            dev = await self._reselect(dev)
            if dev["rssi"] is None:
                log.info(
                    "Still not seen; paging anyway. BlueZ often holds enough to reach a "
                    "machine it has not heard from in the last few seconds."
                )

        # The controller finishes the inquiry window it is in before it pages.
        await asyncio.sleep(RADIO_SETTLE)
        return dev

    async def _reselect(self, dev):
        """Re-read the device after an inquiry; BlueZ may have replaced it."""
        await self.refresh(force=True)
        fresh = self._selected_device()
        if fresh is None:
            raise RuntimeError(
                f"BlueZ no longer knows about {dev['address']}. Scan again with the "
                "machine in pairing mode."
            )
        return fresh

    def answer(self, value=None, accept=True):
        if self._prompt is None:
            raise RuntimeError("nothing is waiting for an answer")
        self._prompt.answer(value, accept)

    def pair(self, pin):
        pin = (pin or "").strip()
        if not pin:
            raise ValueError("the PIN on the machine's screen is needed")
        self._spawn("pair", lambda: self._with_session(lambda s: self._pair(s, pin)))

    def read(self):
        self._spawn("read", lambda: self._read(self.selected))

    async def _read(self, address):
        await self._with_session(lambda s: self._reconnect(s, address), address)

    def forget(self):
        self._spawn("forget", self._forget)

    async def _forget(self):
        dev = self._selected_device()
        if dev is not None and self.adapter:
            try:
                await adapter.remove(self.bus, self.adapter["path"], dev["path"])
                log.info("Removed %s from BlueZ.", dev["address"])
            except Exception as err:  # noqa: BLE001
                log.warning("Could not remove the device from BlueZ: %s", err)
        if self.selected:
            gone = self.selected
            self.publisher.forget(gone)
            store.forget(gone)
            self.readings.pop(gone, None)
        self.selected = store.selected()
        self.session_state = None

    # --- the serial session ------------------------------------------------

    async def _with_session(self, work, address=None):
        address = (address or self.selected or "").upper()
        dev = next((d for d in self.devices if d["address"] == address), None)
        if dev is None:
            raise RuntimeError(f"BlueZ cannot see {address or 'any machine'} right now")
        if not dev["paired"]:
            raise RuntimeError("the Bluetooth bond is missing; do that step first")

        fd = await spp.connect(self.bus, dev["path"], self.profile)
        if fd is None:
            raise RuntimeError("could not open the serial channel; see the log")

        # Imported here so the page still comes up on an image where
        # libairmini failed to build -- the survey is useful on its own.
        from .airmini import Session

        session = Session(fd)
        try:
            session.open()
            await work(session)
        finally:
            self.session_state = session.state
            session.close()
            spp.close(fd)
            try:
                await adapter.disconnect_profile(self.bus, dev["path"])
            except Exception:  # noqa: BLE001 -- the machine may have hung up
                pass

    async def _pair(self, session, pin):
        log.info("Pairing with the machine via SRP-6a.")
        result = await asyncio.wait_for(session.pair(pin), HANDSHAKE_TIMEOUT)
        log.info("Paired. State: %s", session.state)
        key = master_pair_key(result)
        if key:
            store.remember_master_pair_key(self.selected, key)
        else:
            log.warning("No pairing key in the result; the PIN will be needed again.")
            log.warning("The result was %s.", type(result).__name__)
        await self._reads(session, self.selected)

    async def _reconnect(self, session, address):
        key = store.master_pair_key(address)
        if not key:
            raise RuntimeError("no pairing key stored; pair with the PIN first")
        log.info("Reconnecting to %s with its stored key (no PIN needed).", address)
        await asyncio.wait_for(session.open_session(key), HANDSHAKE_TIMEOUT)
        log.info("Session open. State: %s", session.state)
        await self._reads(session, address)

    async def _reads(self, session, address):
        """Everything the machine will say, in one connection.

        Each read is reported on its own rather than aborting on the first
        failure: three of four is a more useful answer than the first one not.
        """
        reads = (
            ("GetVersion", session.get_version),
            ("GetDateTime", session.get_datetime),
            ("Settings", session.get_settings),
            ("MachineMetrics", session.get_metrics),
        )
        results = {}
        failures = 0
        for title, call in reads:
            try:
                value = await asyncio.wait_for(call(), READ_TIMEOUT)
                results[title] = {"ok": True, "value": value}
                _dump(title, value)
            except asyncio.TimeoutError:
                results[title] = {"ok": False, "error": f"timed out after {READ_TIMEOUT}s"}
                log.error("%s timed out after %ds.", title, READ_TIMEOUT)
                failures += 1
            except Exception as err:  # noqa: BLE001
                results[title] = {"ok": False, "error": str(err)}
                log.error("%s failed: %s", title, err)
                failures += 1
        self.readings[address] = {
            "results": results,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            # What Home Assistant is told, which wants an offset rather than
            # a local time with nothing to anchor it.
            "at_iso": _now_iso(),
        }
        self._publish(address)
        if failures:
            log.warning("%d of 4 reads failed.", failures)
        else:
            log.info("All four reads returned.")


def master_pair_key(result):
    """Pull the pairing key out of whatever libairmini handed back.

    Three shapes, because the one that actually arrives is a truncated
    version of the one that was meant to: the object when the JSON survives,
    the `masterPairKey` field lifted out of the text when it does not, and a
    bare key on its own.

    Checked rather than trusted, in every case. Storing something that is not
    the key is a reconnect that fails every night afterwards and looks like
    the machine's fault. Never logged: holding this is what makes a PIN
    unnecessary, so it is as good as the PIN.
    """
    if isinstance(result, dict):
        return _checked(result.get("masterPairKey"))
    if not isinstance(result, str):
        log.warning("The pairing result is a %s, which carries no key.", type(result).__name__)
        return None

    found = _PAIR_KEY_FIELD.search(result)
    if found:
        return _checked(found.group(1))
    key = _checked(result.strip())
    if key is None:
        log.warning(
            "No key in the pairing result: %d characters, no masterPairKey field, not hex.",
            len(result),
        )
    return key


def _checked(candidate):
    if not isinstance(candidate, str) or not _PAIR_KEY.fullmatch(candidate.strip()):
        return None
    key = candidate.strip()
    log.info("Pairing returned a %d-character key.", len(key))
    return key


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def _dump(title, value):

    """Log a result in full, at debug level.

    The page lays readings out now, so this is no longer how anyone reads
    them -- but a whole machine's JSON is exactly what is worth pasting into
    an issue, and at info level ninety lines of it buried everything else in
    the activity log. AIRSUPPLY_LOG_LEVEL=debug brings it back.
    """
    log.debug("--- %s ---", title)
    if value is None:
        log.debug("(empty)")
        return
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    for line in text.splitlines():
        log.debug("%s", line)
