"""What survives a restart.

Exactly one thing, and it is a secret: the masterPairKey the machine hands back
after SRP-6a pairing. Hold it and every later connection is PIN-free -- no
button on the machine, no number typed anywhere. Which is also why it does not
live in the add-on's options: those are rendered in the Home Assistant UI and
land in the Supervisor's backups in the clear.

/data is the add-on's own persistent volume, so it stays on the host and stays
with the add-on when the image is rebuilt.
"""

import json
import logging
import os

log = logging.getLogger("airsupply.store")

PATH = "/data/airsupply.json"


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as err:
        log.warning("Could not read %s (%s); starting empty.", PATH, err)
        return {}


def save(data):
    tmp = f"{PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def master_pair_key(address):
    return load().get("pair_keys", {}).get(address.upper())


def remember_master_pair_key(address, key):
    data = load()
    data.setdefault("pair_keys", {})[address.upper()] = key
    save(data)
    log.info("Stored the pairing key for %s; the PIN is not needed again.", address)
