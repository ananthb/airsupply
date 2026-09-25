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

# Titles for the four reads, in the order a person would want them. The keys
# are what controller._reads() files its results under.
SECTIONS = (
    ("GetVersion", "Firmware"),
    ("GetDateTime", "Machine clock"),
    ("Settings", "Therapy settings"),
    ("MachineMetrics", "Run meters"),
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# The two string shapes the machine answers in that are not words. Run meters
# arrive as ISO-8601 durations ("PT2591392S") and everything dated as an ISO
# instant; both are worth reading as what they are.
_DURATION = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")
_INSTANT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")



def page(raw):
    """The controller's state, shaped for the page."""
    return {
        "version": raw["version"],
        "has_adapter": bool(raw["adapter"]),
        "stage": stage(raw),
        "busy": raw["busy"],
        "scanning": raw["scanning"],
        "machines": [machine(d) for d in raw["devices"]],
        "machine": machine(raw["selected_device"]) if raw["selected_device"] else None,
        "question": question(raw["prompt"]),
        "problem": raw["error"],
        "reading": reading(raw["results"], raw["results_at"]),
        "log": [{"at": l["t"], "level": l["level"], "text": l["msg"]} for l in raw["log"]],
    }


def stage(raw):
    """Which of the five things the page can be showing.

    An ordered walk rather than a set of flags: each step is only reachable
    once the one before it is done, and the page has no business offering a
    PIN box to someone who has not paired yet.
    """
    if not raw["adapter"]:
        return "no-adapter"
    if not raw["selected_device"]:
        return "choose"
    if not raw["bonded"]:
        return "pairing"
    if not raw["has_key"]:
        return "pin"
    return "ready"


def machine(device):
    return {
        "address": device["address"],
        "name": device["name"],
        "signal": device["rssi"],
        "classic": device["classic"],
        "serial": device["spp"],
        "bonded": device["paired"],
        "candidate": device["candidate"],
    }


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


def seconds(value):
    """An ISO-8601 duration as a number, or None if it is not one.

    The run meters arrive as "PT2591392S". This is the same parse the page's
    label uses, kept separate because an entity wants the number and a person
    wants the hours.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, secs = (float(g or 0) for g in match.groups())
    return int(days * 86400 + hours * 3600 + minutes * 60 + secs)


def duration(value):
    """"PT2591392S" is a run meter. Hours are how a CPAP quotes one."""
    total = seconds(value)
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
