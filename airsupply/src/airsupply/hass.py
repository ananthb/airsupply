"""Home Assistant itself: who lives here, and where a reading goes.

Reached over the Supervisor's proxy to the core API, which an add-on gets by
declaring `homeassistant_api: true`; the Supervisor puts the token in the
environment and no credential is configured anywhere.

The part worth reading is why a machine is stored against a person's `id`
rather than their name. A person in Home Assistant has three names and only
one of them is stable:

    entity_id      person.ananth      changes if the person is renamed
    friendly_name  Ananth             changes whenever they feel like it
    attributes.id  01HXV...           assigned once, never reused

So the id is what is written down, and the other two are looked up freshly
every time the page renders. Rename a person in Home Assistant and the link
follows them; delete them and the page says so, rather than showing a name
that no longer belongs to anybody.

Home Assistant has no relation that would say this more strongly. A person
owns device trackers and nothing else -- there is no "these sensors are
hers" -- so the association lives here, and what reaches Home Assistant is
the person's name on the entity and their id in its attributes.
"""

import logging
import os
import re

import aiohttp

log = logging.getLogger("airsupply.hass")

BASE = os.environ.get("AIRSUPPLY_HASS_URL", "http://supervisor/core/api")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
TIMEOUT = aiohttp.ClientTimeout(total=15)

_SLUG = re.compile(r"[^a-z0-9]+")


class Unavailable(Exception):
    """Home Assistant could not be reached, or would not have us."""


class Person:
    """One of Home Assistant's people, as far as we care about them."""

    def __init__(self, state):
        attributes = state.get("attributes") or {}
        self.id = attributes.get("id") or state["entity_id"]
        self.entity_id = state["entity_id"]
        self.name = attributes.get("friendly_name") or state["entity_id"].split(".", 1)[-1]
        self.user_id = attributes.get("user_id")

    @property
    def slug(self):
        return slug(self.name)

    def to_json(self):
        return {"id": self.id, "entity_id": self.entity_id, "name": self.name}


def slug(text):
    return _SLUG.sub("_", str(text).strip().lower()).strip("_") or "person"


def configured():
    """Is there a Home Assistant to talk to at all?

    Without the token the add-on still runs: the page works, the machine can
    be read, and only the part that publishes is off. Which is the right way
    round -- a missing token should not stop someone reading their CPAP.
    """
    return bool(TOKEN)


async def _call(method, path, payload=None):
    if not TOKEN:
        raise Unavailable("no Supervisor token; homeassistant_api is not granted")
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    url = f"{BASE}{path}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.request(method, url, headers=headers, json=payload) as response:
                if response.status == 401:
                    raise Unavailable("Home Assistant refused the Supervisor token")
                if response.status >= 400:
                    raise Unavailable(f"{method} {path} answered {response.status}")
                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()
    except aiohttp.ClientError as err:
        raise Unavailable(str(err) or type(err).__name__) from err


async def people():
    """Everyone Home Assistant knows, newest name first time every time."""
    states = await _call("GET", "/states")
    return [
        Person(state)
        for state in states
        if isinstance(state, dict) and str(state.get("entity_id", "")).startswith("person.")
    ]


async def person(person_id):
    """One person by their stable id, or None if they are gone."""
    for candidate in await people():
        if candidate.id == person_id:
            return candidate
    return None


async def publish(entity_id, state, attributes):
    """Set one entity's state.

    The core API creates an entity on first write, so there is nothing to
    register and nothing to tear down. What it does not give is a device or a
    unique id, which means these cannot be renamed or moved to an area from
    the Home Assistant UI, and they are absent after a restart until the next
    read puts them back. For something written on a schedule that is a fair
    trade for needing no broker and no configuration.
    """
    await _call("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})
