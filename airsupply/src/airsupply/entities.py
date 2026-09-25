"""What a machine looks like in Home Assistant, as plain data.

Discovery payloads and the state document, built without a broker anywhere in
sight so that both can be checked against what Home Assistant expects.

This is the one file that knows libairmini's key names. Everywhere else takes
the machine's JSON as it comes and lays it out; an entity cannot, because
`sensor.ananth_cpap_therapy_hours` has to mean the same thing every night. So
the paths are written down here, each read defensively: a firmware that stops
sending one should cost that entity, not the reading.
"""

import re

# Run meters arrive as ISO-8601 durations ("PT2591392S"). The parse lives here
# rather than with the page's formatting because an entity wants the number;
# turning it into hours for a person is api.py's business.
_DURATION = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def seconds(value):
    """An ISO-8601 duration as a number of seconds, or None if it is not one."""
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, secs = (float(g or 0) for g in match.groups())
    return int(days * 86400 + hours * 3600 + minutes * 60 + secs)


MANUFACTURER = "ResMed"
MODEL = "AirMini"

# One availability topic for the add-on as a whole, set as the broker's last
# will. If the add-on stops, every entity it published goes unavailable rather
# than sitting there showing last night's number as though it were current.
STATUS_TOPIC = "airsupply/status"
ONLINE = "online"
OFFLINE = "offline"

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text):
    return _NOT_SLUG.sub("_", str(text).strip().lower()).strip("_")


def node(address):
    """The address, as something that can go in a topic."""
    return slug(address)


def state_topic(address):
    return f"airsupply/{node(address)}/state"


def attributes_topic(address):
    return f"airsupply/{node(address)}/attributes"


def discovery_topic(component, address, key):
    return f"homeassistant/{component}/airsupply_{node(address)}/{key}/config"


# The entities, and where each one comes from. `read` is the name of the read
# it lives in; `path` is its way through that read's JSON.
SENSORS = (
    {
        "key": "therapy_hours",
        "name": "Therapy hours",
        "read": "MachineMetrics",
        "path": ("MachineMetrics", "TherapyRunMeter"),
        "kind": "hours",
        "device_class": "duration",
        "unit_of_measurement": "h",
        "state_class": "total_increasing",
        "icon": "mdi:timer-outline",
    },
    {
        "key": "machine_hours",
        "name": "Machine hours",
        "read": "MachineMetrics",
        "path": ("MachineMetrics", "MachineRunMeter"),
        "kind": "hours",
        "device_class": "duration",
        "unit_of_measurement": "h",
        "state_class": "total_increasing",
        "icon": "mdi:timer-outline",
        "entity_category": "diagnostic",
    },
    {
        "key": "last_used",
        "name": "Last used",
        "read": "MachineMetrics",
        "path": ("MachineMetrics", "LastTherapyUseDateTime"),
        "kind": "timestamp",
        "device_class": "timestamp",
        "icon": "mdi:sleep",
    },
    {
        "key": "status",
        "name": "Status",
        "read": "Settings",
        "path": ("FGState",),
        "kind": "text",
        "icon": "mdi:air-filter",
    },
    {
        "key": "mode",
        "name": "Therapy mode",
        "read": "Settings",
        "path": ("therapy", "TherapyMode"),
        "kind": "text",
        "icon": "mdi:doctor",
    },
    {
        "key": "pressure_min",
        "name": "Minimum pressure",
        "read": "Settings",
        "path": ("therapy", "MinPressure"),
        "kind": "number",
        "unit_of_measurement": "cmH₂O",
        "state_class": "measurement",
        "icon": "mdi:gauge-low",
    },
    {
        "key": "pressure_max",
        "name": "Maximum pressure",
        "read": "Settings",
        "path": ("therapy", "MaxPressure"),
        "kind": "number",
        "unit_of_measurement": "cmH₂O",
        "state_class": "measurement",
        "icon": "mdi:gauge-full",
    },
    {
        "key": "pressure_set",
        "name": "Set pressure",
        "read": "Settings",
        "path": ("therapy", "SetPressure"),
        "kind": "number",
        "unit_of_measurement": "cmH₂O",
        "state_class": "measurement",
        "icon": "mdi:gauge",
    },
    {
        "key": "last_read",
        "name": "Last read",
        "read": None,
        "path": ("read_at",),
        "kind": "timestamp",
        "device_class": "timestamp",
        "entity_category": "diagnostic",
        "icon": "mdi:clock-check-outline",
    },
)

BINARY_SENSORS = (
    {
        "key": "in_therapy",
        "name": "In therapy",
        "device_class": "running",
        "icon": "mdi:account-clock",
    },
)

COPIED = "|".join(s["key"] for s in SENSORS)


def device(address, machine_name, person_name, firmware):
    """The device every entity for this machine hangs off.

    Named for the person when there is one. Home Assistant has no way to say
    a device belongs to somebody -- a person owns device trackers and nothing
    else -- so the name and the attributes are where the association shows.
    """
    name = machine_name or MODEL
    if person_name:
        name = f"{name} ({person_name})"
    info = {
        "identifiers": [f"airsupply_{node(address)}"],
        "name": name,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "connections": [["mac", address.lower()]],
    }
    if firmware:
        info["sw_version"] = firmware
    return info


def discovery(address, machine_name, person_name, firmware, version, keys):
    """The discovery messages for one machine: (topic, payload) pairs.

    Only for the entities `keys` says this machine reports. A machine on
    AutoSet has no set pressure -- that belongs to the CPAP profile -- and
    announcing one anyway would put a sensor on its device page that reads
    unknown for as long as it owns the machine.

    `keys` comes from a reading, so an entity appears the first time the
    machine mentions it: switch to CPAP and the set pressure turns up at the
    next read.
    """
    info = device(address, machine_name, person_name, firmware)
    origin = {"name": "airsupply", "sw_version": version,
              "support_url": "https://github.com/ananthb/airsupply"}
    out = []

    for sensor in SENSORS:
        if sensor["key"] not in keys:
            continue
        payload = {
            "name": sensor["name"],
            "unique_id": f"airsupply_{node(address)}_{sensor['key']}",
            "object_id": f"airsupply_{node(address)}_{sensor['key']}",
            "state_topic": state_topic(address),
            "value_template": "{{ value_json." + sensor["key"] + " }}",
            "json_attributes_topic": attributes_topic(address),
            "availability_topic": STATUS_TOPIC,
            "payload_available": ONLINE,
            "payload_not_available": OFFLINE,
            "device": info,
            "origin": origin,
        }
        for extra in ("device_class", "unit_of_measurement", "state_class",
                      "entity_category", "icon"):
            if sensor.get(extra):
                payload[extra] = sensor[extra]
        out.append((discovery_topic("sensor", address, sensor["key"]), payload))

    for sensor in BINARY_SENSORS:
        if sensor["key"] not in keys:
            continue
        payload = {
            "name": sensor["name"],
            "unique_id": f"airsupply_{node(address)}_{sensor['key']}",
            "object_id": f"airsupply_{node(address)}_{sensor['key']}",
            "state_topic": state_topic(address),
            "value_template": "{{ value_json." + sensor["key"] + " }}",
            "payload_on": "on",
            "payload_off": "off",
            "json_attributes_topic": attributes_topic(address),
            "availability_topic": STATUS_TOPIC,
            "payload_available": ONLINE,
            "payload_not_available": OFFLINE,
            "device": info,
            "origin": origin,
        }
        for extra in ("device_class", "icon"):
            if sensor.get(extra):
                payload[extra] = sensor[extra]
        out.append((discovery_topic("binary_sensor", address, sensor["key"]), payload))

    return out


def _dig(node_, path):
    for step in path:
        if not isinstance(node_, dict):
            return None
        node_ = node_.get(step)
    return node_


def _active_profile(settings):
    """The therapy profile the machine is actually set to.

    `ActiveTherapyProfile` names one of the profiles in `TherapyProfiles`, so
    the pressures reported are the ones in force rather than all nine of them.
    """
    if not isinstance(settings, dict):
        return {}
    profiles = settings.get("TherapyProfiles")
    active = settings.get("ActiveTherapyProfile")
    if isinstance(profiles, dict) and active in profiles and isinstance(profiles[active], dict):
        return profiles[active]
    return {}


def state(results, read_at):
    """The state document every entity for this machine reads from.

    One retained message rather than one topic per entity: the reading is
    taken in one go and means one thing, so it is published in one go.
    Anything the machine did not report is left out, and Home Assistant shows
    that entity as unknown rather than as a stale number.
    """
    ok = {
        name: value["value"]
        for name, value in (results or {}).items()
        if isinstance(value, dict) and value.get("ok")
    }
    settings = ok.get("Settings") or {}
    sources = {
        "MachineMetrics": ok.get("MachineMetrics") or {},
        "Settings": {**settings, "therapy": _active_profile(settings)},
        None: {"read_at": read_at},
    }

    out = {}
    for sensor in SENSORS:
        raw = _dig(sources.get(sensor["read"], {}), sensor["path"])
        value = _convert(raw, sensor["kind"])
        if value is not None:
            out[sensor["key"]] = value

    status = settings.get("FGState")
    if isinstance(status, str):
        out["in_therapy"] = "on" if status.lower() == "therapy" else "off"
    return out


def _convert(raw, kind):
    if raw is None:
        return None
    if kind == "hours":
        total = seconds(raw)
        return None if total is None else round(total / 3600, 2)
    if kind == "timestamp":
        # Home Assistant wants ISO-8601 with an offset for a timestamp; the
        # machine sends Z, which it does not accept.
        text = str(raw)
        return text.replace("Z", "+00:00") if text.endswith("Z") else text
    if kind == "number":
        return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
    return str(raw)


def attributes(address, person, firmware):
    """What every entity for this machine carries alongside its value.

    The person's stable id is here because this is the only place Home
    Assistant can be told which of its people a reading belongs to.
    """
    out = {"address": address}
    if firmware:
        out["firmware"] = firmware
    if person:
        out["person"] = person.name
        out["person_id"] = person.id
        out["person_entity_id"] = person.entity_id
    return out


def firmware_of(results):
    """The flow generator's application version, for the device page."""
    version = (results or {}).get("GetVersion") or {}
    if not version.get("ok"):
        return None
    return _dig(
        version.get("value"),
        ("FlowGenerator", "IdentificationProfiles", "Software", "ApplicationIdentifier"),
    )
