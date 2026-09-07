"""The one place state lives, and the operations the page can start.

Every operation runs as a background task; the page polls `state()` and
renders whatever is there. One operation at a time, because they all want
the same serial channel, and a person watching a page is not going to click
two things at once on purpose.

Still read-only. Nothing here can reach the machine's Set path; see
airmini/session.py for where that line is drawn.
"""

import asyncio
import collections
import json
import logging
import time

from . import store
from .bluez import adapter, agent, spp

log = logging.getLogger("airsupply")

SCAN_SECONDS = 20
HANDSHAKE_TIMEOUT = 60
READ_TIMEOUT = 30
LOG_LINES = 300


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
        self.results = {}
        self.results_at = None
        self.session_state = None
        self.log = collections.deque(maxlen=LOG_LINES)
        self.agent = None
        self.agent_ok = False
        self.profile = None
        self._prompt = None
        self._last_refresh = 0.0
        self._task = None

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

        if self.selected and store.master_pair_key(self.selected):
            log.info("A pairing key is stored for %s; reading on start.", self.selected)
            self._spawn("read", self._read)

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
            "results": self.results,
            "results_at": self.results_at,
            "session_state": self.session_state,
            "error": self.error,
            "log": list(self.log),
        }

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

    def scan(self):
        if self.scanning:
            return
        if not self.adapter:
            raise RuntimeError("no Bluetooth adapter is visible over D-Bus")
        self.scanning = True

        async def runner():
            path = self.adapter["path"]
            try:
                await adapter.start_discovery(self.bus, path)
                log.info("Scanning for %ds. Put the AirMini in pairing mode now.", SCAN_SECONDS)
                await asyncio.sleep(SCAN_SECONDS)
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

        asyncio.create_task(runner())

    def select(self, address):
        address = (address or "").upper()
        if not any(d["address"] == address for d in self.devices):
            raise ValueError(f"{address} is not a device BlueZ knows about")
        self.selected = address
        store.select(address)
        log.info("Selected %s.", address)

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
        log.info("Bonding with %s. Watch the machine and this page for a code.", dev["address"])
        try:
            await adapter.pair(self.bus, dev["path"])
        finally:
            self.agent.clear()
        log.info("Bonded with %s.", dev["address"])

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
        self._spawn("read", self._read)

    async def _read(self):
        await self._with_session(self._reconnect)

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
            store.forget(self.selected)
            log.info("Forgot %s and its pairing key.", self.selected)
        self.selected = None
        self.results = {}
        self.results_at = None
        self.session_state = None

    # --- the serial session ------------------------------------------------

    async def _with_session(self, work):
        dev = self._selected_device()
        if dev is None:
            raise RuntimeError("no machine selected")
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
        key = result.get("masterPairKey") if isinstance(result, dict) else None
        if key:
            store.remember_master_pair_key(self.selected, key)
        else:
            log.warning("No masterPairKey in the pairing result; the PIN will be needed again.")
            log.warning("Result keys: %s", sorted(result) if isinstance(result, dict) else type(result).__name__)
        await self._reads(session)

    async def _reconnect(self, session):
        key = store.master_pair_key(self.selected)
        if not key:
            raise RuntimeError("no pairing key stored; pair with the PIN first")
        log.info("Reconnecting with the stored pairing key (no PIN needed).")
        await asyncio.wait_for(session.open_session(key), HANDSHAKE_TIMEOUT)
        log.info("Session open. State: %s", session.state)
        await self._reads(session)

    async def _reads(self, session):
        """Experiment 5: the reads libairmini's author verified, reproduced.

        Each is reported on its own rather than aborting on the first
        failure -- three of four working is a more useful result than the
        first one not.
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
        self.results = results
        self.results_at = time.strftime("%Y-%m-%d %H:%M:%S")
        if failures:
            log.warning("%d of 4 reads failed.", failures)
        else:
            log.info("All four reads returned. That is experiment 5 answered.")


def _dump(title, value):
    """Log a JSON result so it can be lifted straight out of the add-on log."""
    log.info("--- %s ---", title)
    if value is None:
        log.info("(empty)")
        return
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    for line in text.splitlines():
        log.info("%s", line)
