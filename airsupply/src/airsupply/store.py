"""What survives a restart: the machines, and what each of them is worth.

Per machine: the masterPairKey it handed back after SRP-6a pairing, and which
of Home Assistant's people it belongs to. Hold the key and every later
connection is PIN-free -- no button on the machine, no number typed anywhere
-- which is exactly why it does not live in the add-on's options: those are
rendered in the Home Assistant UI and land in the Supervisor's backups in the
clear.

The person is stored as Home Assistant's stable `id`, never as a name or an
entity_id; see hass.py for why.

/data is the add-on's own persistent volume, so this stays on the host and
stays with the add-on when the image is rebuilt.
"""

import json
import logging
import os
import threading

log = logging.getLogger("airsupply.store")

PATH = os.environ.get("AIRSUPPLY_STATE", "/data/airsupply.json")
VERSION = 2

# Reads are cheap and writes are rare, but a read-modify-write of one machine
# must not interleave with another's. Everything runs on one event loop, so
# this only guards against a future that does not.
_LOCK = threading.Lock()


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as handle:
            return migrate(json.load(handle))
    except FileNotFoundError:
        return empty()
    except (OSError, json.JSONDecodeError) as err:
        log.warning("Could not read %s (%s); starting empty.", PATH, err)
        return empty()


def empty():
    return {"version": VERSION, "machines": {}, "selected": None}


def migrate(data):
    """Bring a file written by an older add-on up to date.

    Version 1 held one chosen machine and a flat table of pairing keys. Every
    key in it is worth keeping -- it is the thing that means nobody has to
    stand at the machine again -- so they become machines with no person yet.
    """
    if not isinstance(data, dict):
        return empty()
    if data.get("version") == VERSION and isinstance(data.get("machines"), dict):
        data.setdefault("selected", None)
        return data

    keys = data.get("pair_keys") or {}
    machines = {
        address.upper(): {"name": "", "key": key, "person_id": None}
        for address, key in keys.items()
        if isinstance(key, str)
    }
    selected = data.get("selected")
    if selected:
        machines.setdefault(selected.upper(), {"name": "", "key": None, "person_id": None})
    return {"version": VERSION, "machines": machines, "selected": selected}


def upgrade():
    """Write the file back in the current format, once, at start-up.

    load() already upgrades whatever it reads, but only in memory -- so until
    something happened to write, every read upgraded again, and the page polls
    four times a minute. Saying so once and getting it on disk keeps it out of
    the log and off the path of every poll.
    """
    try:
        with open(PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict) or raw.get("version") == VERSION:
        return
    carried = len(load()["machines"])
    _edit(lambda data: None)
    log.info("Upgraded %s, carrying %d machine(s) over.", PATH, carried)


def save(data):
    tmp = f"{PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def _edit(change):
    with _LOCK:
        data = load()
        result = change(data)
        save(data)
        return result


def _slot(data, address):
    return data["machines"].setdefault(
        address.upper(), {"name": "", "key": None, "person_id": None}
    )


# --- reading ---------------------------------------------------------------


def machines():
    """Every machine, address to record. The record never carries the key."""
    return {
        address: {"name": m.get("name") or "", "person_id": m.get("person_id"), "paired": bool(m.get("key"))}
        for address, m in load()["machines"].items()
    }


def selected():
    return load().get("selected")


def master_pair_key(address):
    if not address:
        return None
    return (load()["machines"].get(address.upper()) or {}).get("key")


def person_id(address):
    if not address:
        return None
    return (load()["machines"].get(address.upper()) or {}).get("person_id")


# --- writing ---------------------------------------------------------------


def add(address, name=""):
    """Know about a machine, whether or not it has ever been paired with."""
    def change(data):
        slot = _slot(data, address)
        if name:
            slot["name"] = name
    _edit(change)


def select(address):
    def change(data):
        if address:
            _slot(data, address)
            data["selected"] = address.upper()
        else:
            data["selected"] = None
    _edit(change)
    if address:
        log.info("Looking at %s.", address)


def remember_master_pair_key(address, key):
    def change(data):
        _slot(data, address)["key"] = key
    _edit(change)
    log.info("Stored the pairing key for %s; the PIN is not needed again.", address)


def assign(address, person):
    """Tie a machine to one of Home Assistant's people, by their stable id."""
    def change(data):
        _slot(data, address)["person_id"] = person
    _edit(change)
    log.info("%s belongs to person %s.", address, person or "nobody")


def forget(address):
    def change(data):
        data["machines"].pop(address.upper(), None)
        if (data.get("selected") or "").upper() == address.upper():
            data["selected"] = next(iter(data["machines"]), None)
    _edit(change)
    log.info("Forgot %s, its pairing key and its person.", address)
