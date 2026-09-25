"""The contract between api.page() and the page's decoders.

Elm decodes the state strictly: a missing field is a decode error and the page
shows nothing but "the add-on is not answering", which looks like a dead
add-on rather than a renamed key. The names below mirror the `D.field` calls
in web/src/Main.elm, and test_decoders.py checks they have not drifted.
"""

import unittest

from airsupply import api

STATE = frozenset({
    "version", "has_adapter", "stage", "busy", "scanning",
    "machines", "machine", "found", "people", "people_problem", "publishing",
    "question", "problem", "summary", "reading", "log",
})
MACHINE = frozenset({
    "address", "name", "signal", "classic", "serial", "bonded", "paired",
    "connected", "released", "person", "person_id", "missing_person", "last_read",
})
FOUND = frozenset({"address", "name", "signal", "classic", "serial", "bonded", "candidate"})
PERSON = frozenset({"id", "entity_id", "name"})
PUBLISHING = frozenset({"configured", "connected", "problem"})
READING = frozenset({"at", "sections"})
SECTION = frozenset({"title", "ok", "problem", "fields"})
FIELD = frozenset({"label", "value"})
QUESTION = frozenset({"kind", "code"})
LINE = frozenset({"at", "level", "text"})

ADDRESS = "28:68:47:18:C2:78"
PERSON_ROW = {"id": "01HXVPERSON", "entity_id": "person.ananth", "name": "Ananth"}

DEVICE = {
    "path": "/org/bluez/hci0/dev_28", "address": ADDRESS, "name": "AirMini", "rssi": -57,
    "classic": True, "class": "0x0", "spp": True, "paired": True,
    "connected": True, "trusted": True, "candidate": True,
}
MACHINE_ROW = {
    "address": ADDRESS, "name": "AirMini", "signal": -57, "classic": True, "serial": True,
    "bonded": True, "paired": True, "connected": True, "released": False,
    "person": PERSON_ROW, "person_id": PERSON_ROW["id"],
    "missing_person": False, "last_read": "2026-09-25 20:12:10",
}
RESULTS = {
    "Settings": {"ok": True, "value": {
        "FGState": "Standby", "ActiveTherapyProfile": "AutoSetProfile",
        "TherapyProfiles": {"AutoSetProfile": {
            "MinPressure": 5.0, "MaxPressure": 20.0, "TherapyMode": "AutoSet"}},
    }},
    "MachineMetrics": {"ok": True, "value": {"MachineMetrics": {
        "TherapyRunMeter": "PT2589645S", "LastTherapyUseDateTime": "2026-09-25T06:26:51.000Z"}}},
}


def raw(**over):
    state = {
        "version": "0.4.0", "busy": None, "scanning": False, "agent_ok": True,
        "adapter": {"path": "/org/bluez/hci0", "address": "AA:BB", "name": "hci0",
                    "powered": True, "discovering": True},
        "devices": [DEVICE],
        "selected": ADDRESS,
        "machines": [dict(MACHINE_ROW)],
        "people": [PERSON_ROW],
        "people_problem": None,
        "publishing": {"configured": True, "connected": True, "problem": None},
        "reading": {"results": RESULTS, "at": "2026-09-25 20:12:10",
                    "at_iso": "2026-09-25T20:12:10+00:00"},
        "prompt": None, "session_state": "session", "error": None,
        "log": [{"t": "20:12", "level": "info", "msg": "Session open."}],
    }
    state.update(over)
    return state


def only(**over):
    """One machine row, changed."""
    return raw(machines=[{**MACHINE_ROW, **over}])


class Contract(unittest.TestCase):
    def test_every_field_the_page_decodes_is_present(self):
        page = api.page(raw(prompt={"kind": "confirm", "device": "/d", "value": "418921"}))
        self.assertEqual(STATE, set(page))
        self.assertEqual(MACHINE, set(page["machine"]))
        self.assertEqual(MACHINE, set(page["machines"][0]))
        self.assertEqual(FOUND, set(page["found"][0]))
        self.assertEqual(PERSON, set(page["people"][0]))
        self.assertEqual(PERSON, set(page["machine"]["person"]))
        self.assertEqual(PUBLISHING, set(page["publishing"]))
        self.assertEqual(QUESTION, set(page["question"]))
        self.assertEqual(LINE, set(page["log"][0]))
        self.assertEqual(READING, set(page["reading"]))
        for section in page["reading"]["sections"]:
            self.assertEqual(SECTION, set(section))
            for field in section["fields"]:
                self.assertEqual(FIELD, set(field))
        for row in page["summary"]:
            self.assertEqual(FIELD, set(row))

    def test_nullable_fields_are_null_not_absent(self):
        page = api.page(raw(selected=None, machines=[], reading=None, people=[]))
        for key in ("machine", "question", "problem", "reading", "busy", "people_problem"):
            self.assertIsNone(page[key], key)
        self.assertEqual([], page["summary"])

    def test_stage_walks_setup_in_order(self):
        self.assertEqual("no-adapter", api.page(raw(adapter=None))["stage"])
        self.assertEqual("choose", api.page(raw(selected=None, machines=[]))["stage"])
        self.assertEqual("pairing", api.page(only(bonded=False, paired=False))["stage"])
        self.assertEqual("pin", api.page(only(bonded=True, paired=False))["stage"])
        self.assertEqual("ready", api.page(raw())["stage"])

    def test_a_machine_out_of_range_is_still_set_up(self):
        # BlueZ forgets a device it has not heard from. That is not the same
        # as the machine being unpaired, and the page must not send someone
        # back to the PIN box because their CPAP is asleep.
        page = api.page(only(bonded=False, signal=None, paired=True))
        self.assertEqual("ready", page["stage"])


class Summary(unittest.TestCase):
    """The few facts a reading is about, above everything it contains."""

    def test_it_leads_with_when_the_machine_was_last_used(self):
        rows = api.page(raw())["summary"]
        self.assertEqual("Last used", rows[0]["label"])
        self.assertRegex(rows[0]["value"], r"^2[45] Sep 2026, \d\d:\d\d$")

    def test_hours_are_hours_and_minutes(self):
        rows = {r["label"]: r["value"] for r in api.page(raw())["summary"]}
        self.assertEqual("719 h 21 min", rows["Therapy hours"])

    def test_a_pressure_range_is_one_row_not_two(self):
        rows = {r["label"]: r["value"] for r in api.page(raw())["summary"]}
        self.assertEqual("5-20 cmH2O", rows["Pressure"])
        self.assertEqual("AutoSet", rows["Mode"])
        self.assertEqual("Standby", rows["Status"])

    def test_nothing_read_means_nothing_to_summarise(self):
        self.assertEqual([], api.page(raw(reading=None))["summary"])


class Fields(unittest.TestCase):
    """Whatever the machine returns has to come out as rows a person can read."""

    def test_nested_values_keep_their_parent_in_the_label(self):
        rows = api.fields({"therapyProfiles": [{"startPressure": 4.0}, {"startPressure": 10.4}]})
        self.assertEqual(
            [("Therapy profiles 1 start pressure", "4"), ("Therapy profiles 2 start pressure", "10.4")],
            [(r["label"], r["value"]) for r in rows],
        )

    def test_scalars_are_readable_rather_than_literal(self):
        rows = {r["label"]: r["value"] for r in api.fields(
            {"smartStart": True, "rampTime": None, "maskType": "nasal", "pressure": 9.0}
        )}
        self.assertEqual({"Smart start": "yes", "Ramp time": "not set",
                          "Mask type": "nasal", "Pressure": "9"}, rows)

    def test_a_list_of_scalars_stays_one_row(self):
        self.assertEqual(
            [{"label": "Modes", "value": "cpap, apap"}],
            api.fields({"modes": ["cpap", "apap"]}),
        )

    def test_a_bare_string_result_is_still_a_row(self):
        self.assertEqual([{"label": "Value", "value": "SX567-0101"}], api.fields("SX567-0101"))


if __name__ == "__main__":
    unittest.main()
