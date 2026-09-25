"""What Home Assistant is told, built from what the machine actually said.

No broker here. Discovery payloads and the state document are plain data, so
the thing worth checking is that they say what Home Assistant expects: every
entity unique, every value template pointing at a key the state document
really has, and a reading that came back wrong costing one entity rather than
the lot.
"""

import json
import unittest

from airsupply import entities
from test_readings import METRICS, SETTINGS

ADDRESS = "28:68:47:18:C2:78"
NODE = "28_68_47_18_c2_78"
VERSION = {
    "FlowGenerator": {
        "IdentificationProfiles": {
            "Software": {"ApplicationIdentifier": "SW03900.01.4.0.3.50927"}
        }
    }
}
RESULTS = {
    "GetVersion": {"ok": True, "value": VERSION},
    "Settings": {"ok": True, "value": SETTINGS},
    "MachineMetrics": {"ok": True, "value": METRICS},
}
READ_AT = "2026-09-25T20:12:10+00:00"


class Person:
    id = "01HXVPERSON"
    name = "Ananth"
    entity_id = "person.ananth"


class StateDocument(unittest.TestCase):
    def test_the_run_meters_become_hours_a_number(self):
        state = entities.state(RESULTS, READ_AT)
        self.assertEqual(719.35, state["therapy_hours"])
        self.assertEqual(719.83, state["machine_hours"])

    def test_pressures_come_from_the_profile_actually_in_force(self):
        # The machine is on AutoSetProfile; CpapProfile's 10 cmH2O is not in
        # use and must not be what the entity reports.
        state = entities.state(RESULTS, READ_AT)
        self.assertEqual(5.0, state["pressure_min"])
        self.assertEqual(20.0, state["pressure_max"])
        self.assertEqual("AutoSet", state["mode"])
        self.assertNotIn("pressure_set", state)

    def test_a_timestamp_is_offset_not_zulu(self):
        # Home Assistant rejects a Z-suffixed timestamp for device_class
        # timestamp; the machine sends nothing else.
        state = entities.state(RESULTS, READ_AT)
        self.assertEqual("2026-09-25T06:26:51.000+00:00", state["last_used"])
        self.assertNotIn("Z", state["last_used"])

    def test_standby_is_not_in_therapy(self):
        self.assertEqual("off", entities.state(RESULTS, READ_AT)["in_therapy"])

    def test_therapy_is(self):
        running = {**RESULTS, "Settings": {"ok": True, "value": {**SETTINGS, "FGState": "Therapy"}}}
        self.assertEqual("on", entities.state(running, READ_AT)["in_therapy"])

    def test_one_failed_read_costs_only_its_own_entities(self):
        partial = {**RESULTS, "MachineMetrics": {"ok": False, "error": "timed out after 30s"}}
        state = entities.state(partial, READ_AT)
        self.assertNotIn("therapy_hours", state)
        self.assertNotIn("last_used", state)
        self.assertEqual("AutoSet", state["mode"])
        self.assertEqual("Standby", state["status"])

    def test_nothing_read_at_all_still_says_when_we_tried(self):
        self.assertEqual({"last_read": READ_AT}, entities.state({}, READ_AT))


class Discovery(unittest.TestCase):
    def setUp(self):
        self.state = entities.state(RESULTS, READ_AT)
        self.messages = entities.discovery(
            ADDRESS, "AirMini", "Ananth", entities.firmware_of(RESULTS), "0.4.0", set(self.state)
        )
        self.payloads = [payload for _, payload in self.messages]

    def test_every_entity_has_its_own_unique_id(self):
        ids = [p["unique_id"] for p in self.payloads]
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertTrue(all(i.startswith(f"airsupply_{NODE}_") for i in ids))

    def test_every_value_template_reads_a_key_the_state_document_has(self):
        for payload in self.payloads:
            key = payload["value_template"].strip("{} ").split(".", 1)[1].strip()
            with self.subTest(entity=payload["unique_id"]):
                self.assertIn(key, self.state)

    def test_every_entity_hangs_off_one_device_named_for_its_person(self):
        devices = {json.dumps(p["device"], sort_keys=True) for p in self.payloads}
        self.assertEqual(1, len(devices))
        device = self.payloads[0]["device"]
        self.assertEqual("AirMini (Ananth)", device["name"])
        self.assertEqual([f"airsupply_{NODE}"], device["identifiers"])
        self.assertEqual("SW03900.01.4.0.3.50927", device["sw_version"])
        self.assertEqual([["mac", "28:68:47:18:c2:78"]], device["connections"])

    def test_a_machine_with_no_person_is_still_a_device(self):
        anonymous = entities.discovery(ADDRESS, "AirMini", None, None, "0.4.0", set(self.state))
        device = anonymous[0][1]["device"]
        self.assertEqual("AirMini", device["name"])
        self.assertNotIn("sw_version", device)

    def test_everything_goes_unavailable_together_if_the_add_on_stops(self):
        for payload in self.payloads:
            self.assertEqual(entities.STATUS_TOPIC, payload["availability_topic"])

    def test_discovery_topics_are_where_home_assistant_looks(self):
        topics = [topic for topic, _ in self.messages]
        self.assertIn(f"homeassistant/sensor/airsupply_{NODE}/therapy_hours/config", topics)
        self.assertIn(f"homeassistant/binary_sensor/airsupply_{NODE}/in_therapy/config", topics)

    def test_an_entity_the_machine_does_not_report_is_not_announced(self):
        # This machine is on AutoSet, which has no set pressure. Announcing
        # one would sit on its device page reading unknown for ever.
        ids = [p["unique_id"] for p in self.payloads]
        self.assertNotIn("airsupply_" + NODE + "_pressure_set", ids)

    def test_it_appears_once_the_machine_starts_reporting_it(self):
        on_cpap = {**SETTINGS, "ActiveTherapyProfile": "CpapProfile"}
        state = entities.state({**RESULTS, "Settings": {"ok": True, "value": on_cpap}}, READ_AT)
        ids = [p["unique_id"] for _, p in
               entities.discovery(ADDRESS, "AirMini", "Ananth", None, "0.4.0", set(state))]
        self.assertIn("airsupply_" + NODE + "_pressure_set", ids)
        self.assertEqual(10.0, state["pressure_set"])

    def test_the_person_is_on_every_entity_by_id(self):
        carried = entities.attributes(ADDRESS, Person(), "SW03900.01.4.0.3.50927")
        self.assertEqual("01HXVPERSON", carried["person_id"])
        self.assertEqual("person.ananth", carried["person_entity_id"])
        self.assertEqual("Ananth", carried["person"])
        for payload in self.payloads:
            self.assertEqual(entities.attributes_topic(ADDRESS), payload["json_attributes_topic"])


if __name__ == "__main__":
    unittest.main()
