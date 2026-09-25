"""The page's view of the add-on: one JSON shape, derived from the controller.

The page asks one question -- what is going on -- and gets the whole answer, so
there is nothing on the browser side to merge and nothing to go stale. Every
action answers with the same shape for the same reason.

The part worth reading is `fields`. A read comes back from libairmini as
whatever JSON the machine sent, whose keys are not ours and are not stable, so
nothing here can name them in advance. Flattening the result into labelled rows
lets the page lay a reading out as a reading rather than print it as JSON, and
keeps working when the machine returns a key we have never seen.
"""

import collections
import datetime
import re

from . import entities

# Titles for the four reads, in the order a person would want them. The keys
# are what controller._reads() files its results under.
SECTIONS = (
    ("GetVersion", "Firmware"),
    ("GetDateTime", "Machine clock"),
    ("Settings", "Therapy settings"),
    ("MachineMetrics", "Run meters"),
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Everything dated arrives as an ISO instant, which is worth reading as one.
# The other shape, an ISO-8601 duration, is parsed in entities.py: an entity
# wants the number and only this file wants it in hours.
_INSTANT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")



def page(raw):
    """The controller's state, shaped for the page."""
    chosen = next((m for m in raw["machines"] if m["address"] == raw["selected"]), None)
    reading_now = raw["reading"] or {}
    results = reading_now.get("results")
    return {
        "version": raw["version"],
        "has_adapter": bool(raw["adapter"]),
        "stage": stage(raw, chosen),
        "busy": raw["busy"],
        "scanning": raw["scanning"],
        "machines": raw["machines"],
        "machine": chosen,
        "found": [found(d) for d in raw["devices"]],
        "people": raw["people"],
        "people_problem": raw["people_problem"],
        "publishing": raw["publishing"],
        "question": question(raw["prompt"]),
        "problem": raw["error"],
        "summary": summary(results, reading_now.get("at_iso")),
        "reading": reading(results, reading_now.get("at")),
        "log": [{"at": l["t"], "level": l["level"], "text": l["msg"]} for l in raw["log"]],
    }


def stage(raw, chosen):
    """Which of the five things the page can be showing.

    An ordered walk rather than a set of flags: each step is only reachable
    once the one before it is done, and the page has no business offering a
    PIN box to someone who has not paired yet.
    """
    if not raw["adapter"]:
        return "no-adapter"
    if chosen is None:
        return "choose"
    if not chosen["paired"] and not chosen["bonded"]:
        return "pairing"
    if not chosen["paired"]:
        return "pin"
    return "ready"


def found(device):
    """A device BlueZ can see, which may or may not be a machine of ours."""
    return {
        "address": device["address"],
        "name": device["name"],
        "signal": device["rssi"],
        "classic": device["classic"],
        "serial": device["spp"],
        "bonded": device["paired"],
        "candidate": device["candidate"],
    }


# The handful of things worth reading before anything else, in the order a
# person would want them. Everything else stays in the reading below.
SUMMARY = (("last_used", "Last used"), ("therapy_hours", "Therapy hours"),
           ("status", "Status"), ("mode", "Mode"))


def summary(results, at_iso):
    """The few facts a reading is actually about.

    Built from the same document the Home Assistant entities are, so the page
    and the sensors cannot drift into saying different things about the same
    night.
    """
    if not results:
        return []
    state = entities.state(results, at_iso)
    rows = [{"label": label, "value": _readable(key, state[key])}
            for key, label in SUMMARY if key in state]
    pressure = _pressure(state)
    if pressure:
        rows.append({"label": "Pressure", "value": pressure})
    return rows


def _readable(key, value):
    if key.endswith("_hours"):
        hours = int(value)
        return f"{hours} h {int(round((value - hours) * 60)):02d} min"
    if key in ("last_used", "last_read"):
        return instant(value) or str(value)
    return str(value)


def _pressure(state):
    low, high, set_to = state.get("pressure_min"), state.get("pressure_max"), state.get("pressure_set")
    if low is not None and high is not None:
        return f"{scalar(low)}-{scalar(high)} cmH2O"
    if set_to is not None:
        return f"{scalar(set_to)} cmH2O"
    return None


def question(prompt):
    if not prompt:
        return None
    return {"kind": prompt["kind"], "code": prompt.get("value")}


def reading(results, at):
    if not results:
        return None
    titles = dict(SECTIONS)
    order = [name for name, _ in SECTIONS if name in results]
    order += [name for name in results if name not in titles]
    return {
        "at": at or "",
        "sections": [
            {
                "title": titles.get(name, humanise(name)),
                "ok": results[name]["ok"],
                "problem": None if results[name]["ok"] else results[name]["error"],
                "fields": fields(results[name]["value"]) if results[name]["ok"] else [],
            }
            for name in order
        ],
    }


def fields(value):
    """Flatten one read result into the labelled rows the page lays out.

    A row is named after its own key, which is what someone reading a therapy
    setting wants to see. Where that name is not unique -- every profile has a
    start pressure, and both subsystems report an application identifier --
    the row also carries the step of its path that actually tells it apart.

    Which is not necessarily its parent. The two application identifiers are
    both under `IdentificationProfiles/Software`; they differ three levels up,
    at `FlowGenerator` against `BluetoothModule`. Naming them by their parent
    would call them both "software application identifier" and lose which
    machine part each belongs to, which is the one thing the label is for.
    """
    rows = []
    _walk([], value, rows)
    paths = [_units(path) for path, _ in rows]
    names = [_leaf(path) for path in paths]

    together = collections.defaultdict(list)
    for path, name in zip(paths, names):
        together[name].append(path)

    return [
        {"label": _label(path, name, together[name]), "value": text}
        for (_, text), path, name in zip(rows, paths, names)
    ]


def _units(path):
    """Fold a list index into the name above it: "profiles 2", not "2"."""
    units = []
    for step in path:
        if step.isdigit() and units:
            units[-1] = units[-1] + " " + step
        else:
            units.append(step)
    return units


def _leaf(units):
    return humanise(units[-1]) if units else "Value"


def _label(path, name, sharing):
    if len(sharing) == 1:
        return name
    telling = _telling(path, sharing)
    if telling is None:
        return name
    return humanise(telling) + " " + name[:1].lower() + name[1:]


def _telling(path, sharing):
    """The shallowest step of this path that not everything sharing the name has."""
    for index in range(len(path) - 1):
        step = path[index]
        if any(index >= len(other) or other[index] != step for other in sharing):
            return step
    return None


def _walk(path, node, rows):
    if isinstance(node, dict):
        if not node:
            rows.append((path, "empty"))
        for key, child in node.items():
            _walk(path + [str(key)], child, rows)
    elif isinstance(node, list):
        if not node:
            rows.append((path, "none"))
        elif all(not isinstance(item, (dict, list)) for item in node):
            rows.append((path, ", ".join(scalar(item) for item in node)))
        else:
            for index, item in enumerate(node, 1):
                _walk(path + [str(index)], item, rows)
    else:
        rows.append((path, scalar(node)))


def scalar(value):
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        # Machine settings come back as 4.0 and 10.4 alike; a trailing .0 on
        # a pressure reads as false precision.
        return f"{value:g}"
    if isinstance(value, str):
        return duration(value) or instant(value) or value
    return str(value)


def duration(value):
    """"PT2591392S" is a run meter. Hours are how a CPAP quotes one."""
    total = entities.seconds(value)
    if total is None:
        return None
    if total < 3600:
        return f"{total // 60} min"
    return f"{total // 3600} h {total % 3600 // 60:02d} min"


def instant(value):
    """An ISO timestamp, in the add-on's own timezone rather than as UTC.

    Home Assistant sets the container's timezone, so this is the clock the
    person reading the page is on -- which for "last used" is the whole point
    of showing it.
    """
    if not _INSTANT.match(value):
        return None
    try:
        when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.strftime("%d %b %Y, %H:%M")


def humanise(name):
    words = _CAMEL.sub(" ", str(name)).replace("_", " ").replace("-", " ").split()
    if not words:
        return str(name)
    first, rest = words[0], words[1:]
    return " ".join([first[:1].upper() + first[1:]] + [w if w.isupper() else w.lower() for w in rest])
