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
    setting wants to see. Only where that would produce the same name twice --
    every profile has a start pressure -- does a row take its parent's name
    as well, so the short labels stay short.
    """
    rows = []
    _walk([], value, rows)
    names = [_leaf(path) for path, _ in rows]
    repeated = {name for name, count in collections.Counter(names).items() if count > 1}
    return [
        {"label": _qualified(path) if name in repeated else name, "value": text}
        for (path, text), name in zip(rows, names)
    ]


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
    match = _DURATION.fullmatch(value)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (float(g or 0) for g in match.groups())
    total = int(days * 86400 + hours * 3600 + minutes * 60 + seconds)
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


def _leaf(path):
    named = [step for step in path if not step.isdigit()]
    return humanise(named[-1]) if named else "Value"


def _qualified(path):
    """The row's own name, with as much of its parent as it takes to be unique.

    A list index belongs to the name above it -- "therapy profiles 2", not a
    step of its own -- or the label starts with a bare number.
    """
    units = []
    for step in path:
        if step.isdigit() and units:
            units[-1] = units[-1] + " " + step
        else:
            units.append(step)
    return humanise(" ".join(units[-2:])) if units else "Value"


def humanise(name):
    words = _CAMEL.sub(" ", str(name)).replace("_", " ").replace("-", " ").split()
    if not words:
        return str(name)
    first, rest = words[0], words[1:]
    return " ".join([first[:1].upper() + first[1:]] + [w if w.isupper() else w.lower() for w in rest])
