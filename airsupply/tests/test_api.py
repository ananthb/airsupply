"""The contract between api.page() and the page's decoders.

Elm decodes the state strictly: a missing field is a decode error and the page
shows nothing but "the add-on is not answering", which looks like a dead
add-on rather than a renamed key. The names below mirror the `D.field` calls in
web/src/Main.elm, so renaming one on either side fails the image build.
"""

import unittest

from airsupply import api

STATE = frozenset({
    "version", "has_adapter", "stage", "busy", "scanning",
    "machines", "machine", "question", "problem", "reading", "log",
})
MACHINE = frozenset({"address", "name", "signal", "classic", "serial", "bonded", "candidate"})
READING = frozenset({"at", "sections"})
SECTION = frozenset({"title", "ok", "problem", "fields"})
FIELD = frozenset({"label", "value"})
QUESTION = frozenset({"kind", "code"})
LINE = frozenset({"at", "level", "text"})

DEVICE = {
    "path": "/org/bluez/hci0/dev_8C_DE_00_00_00_01",
    "address": "8C:DE:00:00:00:01", "name": "AirMini", "rssi": -57,
    "classic": True, "class": "0x000000", "spp": True, "paired": True,
    "connected": True, "trusted": True, "candidate": True,
}


def raw(**over):
    state = {
        "version": "0.3.0", "busy": None, "scanning": False, "agent_ok": True,
        "adapter": {"path": "/org/bluez/hci0", "address": "AA:BB", "name": "hci0",
                    "powered": True, "discovering": True},
        "devices": [DEVICE], "selected": DEVICE["address"], "selected_device": DEVICE,
        "bonded": True, "has_key": True, "prompt": None,
        "results": {}, "results_at": None, "session_state": "session",
        "error": None, "log": [{"t": "21:04:02", "level": "info", "msg": "Session open."}],
    }
    state.update(over)
    return state


class Contract(unittest.TestCase):
    def test_every_field_the_page_decodes_is_present(self):
        page = api.page(raw(
            prompt={"kind": "confirm", "device": "/d", "value": "418921"},
            results={"GetVersion": {"ok": True, "value": {"fwVersion": "1.8.0"}},
                     "Settings": {"ok": False, "error": "timed out after 30s"}},
            results_at="2026-09-25 21:04:11",
        ))
        self.assertEqual(STATE, set(page))
        self.assertEqual(MACHINE, set(page["machine"]))
        self.assertEqual(MACHINE, set(page["machines"][0]))
        self.assertEqual(QUESTION, set(page["question"]))
        self.assertEqual(LINE, set(page["log"][0]))
        self.assertEqual(READING, set(page["reading"]))
        for section in page["reading"]["sections"]:
            self.assertEqual(SECTION, set(section))
            for field in section["fields"]:
                self.assertEqual(FIELD, set(field))

    def test_nullable_fields_are_null_not_absent(self):
        page = api.page(raw(selected=None, selected_device=None, devices=[]))
        for key in ("machine", "question", "problem", "reading", "busy"):
            self.assertIsNone(page[key], key)

    def test_stage_walks_setup_in_order(self):
        self.assertEqual("no-adapter", api.page(raw(adapter=None))["stage"])
        self.assertEqual("choose", api.page(raw(selected_device=None))["stage"])
        self.assertEqual("pairing", api.page(raw(bonded=False))["stage"])
        self.assertEqual("pin", api.page(raw(has_key=False))["stage"])
        self.assertEqual("ready", api.page(raw())["stage"])

    def test_a_failed_read_carries_its_reason_and_no_rows(self):
        section = api.page(raw(
            results={"MachineMetrics": {"ok": False, "error": "timed out after 30s"}},
            results_at="2026-09-25 21:04:11",
        ))["reading"]["sections"][0]
        self.assertFalse(section["ok"])
        self.assertEqual("timed out after 30s", section["problem"])
        self.assertEqual([], section["fields"])


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
