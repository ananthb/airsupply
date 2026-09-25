"""The store, and the upgrade that must not lose a pairing key.

A key is the thing that means nobody has to stand at the machine again, so
the one operation here that really matters is reading a file written by an
older add-on and coming out the other side still holding it.
"""

import json
import os
import tempfile
import unittest

V1 = {"selected": "28:68:47:18:C2:78", "pair_keys": {"28:68:47:18:c2:78": "ab" * 32}}


class StoreCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.path)
        os.environ["AIRSUPPLY_STATE"] = self.path
        # Imported late and reloaded, because PATH is read at import time.
        import importlib

        from airsupply import store as module

        self.store = importlib.reload(module)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        os.environ.pop("AIRSUPPLY_STATE", None)

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)


class Upgrade(StoreCase):
    def test_a_version_1_file_keeps_its_key_and_its_choice(self):
        self.write(V1)
        self.assertEqual("ab" * 32, self.store.master_pair_key("28:68:47:18:C2:78"))
        self.assertEqual("28:68:47:18:C2:78", self.store.selected())
        self.assertEqual(["28:68:47:18:C2:78"], list(self.store.machines()))

    def test_an_upgraded_machine_has_no_person_yet(self):
        self.write(V1)
        self.assertIsNone(self.store.person_id("28:68:47:18:C2:78"))

    def test_the_upgrade_is_written_back_and_is_stable(self):
        self.write(V1)
        self.store.select("28:68:47:18:C2:78")
        again = self.store.load()
        self.assertEqual(2, again["version"])
        self.assertEqual("ab" * 32, self.store.master_pair_key("28:68:47:18:C2:78"))

    def test_upgrade_puts_the_new_format_on_disk(self):
        self.write(V1)
        self.store.upgrade()
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(2, json.load(handle)["version"])

    def test_reading_never_says_anything(self):
        # The bug this pins: load() upgraded in memory and announced it, and
        # the page polls four times a minute, so the log filled with
        # "carried 1 machine over" for ever.
        self.write(V1)
        self.store.upgrade()
        with self.assertNoLogs("airsupply.store", level="INFO"):
            for _ in range(5):
                self.store.machines()
                self.store.selected()
                self.store.master_pair_key("28:68:47:18:C2:78")

    def test_upgrading_twice_is_quiet_the_second_time(self):
        self.write(V1)
        self.store.upgrade()
        with self.assertNoLogs("airsupply.store", level="INFO"):
            self.store.upgrade()

    def test_upgrade_leaves_a_file_that_was_never_written_alone(self):
        self.store.upgrade()
        self.assertFalse(os.path.exists(self.path))

    def test_rubbish_on_disk_does_not_take_the_add_on_down(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        self.assertEqual({}, self.store.machines())


class Machines(StoreCase):
    def test_several_machines_each_keep_their_own_key_and_person(self):
        self.store.add("AA:AA:AA:AA:AA:AA", "AirMini")
        self.store.add("BB:BB:BB:BB:BB:BB", "AirMini")
        self.store.remember_master_pair_key("AA:AA:AA:AA:AA:AA", "aa" * 32)
        self.store.remember_master_pair_key("BB:BB:BB:BB:BB:BB", "bb" * 32)
        self.store.assign("AA:AA:AA:AA:AA:AA", "person-one")
        self.store.assign("BB:BB:BB:BB:BB:BB", "person-two")

        self.assertEqual("aa" * 32, self.store.master_pair_key("AA:AA:AA:AA:AA:AA"))
        self.assertEqual("bb" * 32, self.store.master_pair_key("BB:BB:BB:BB:BB:BB"))
        self.assertEqual("person-one", self.store.person_id("AA:AA:AA:AA:AA:AA"))
        self.assertEqual("person-two", self.store.person_id("BB:BB:BB:BB:BB:BB"))

    def test_the_listing_never_carries_a_key(self):
        self.store.add("AA:AA:AA:AA:AA:AA")
        self.store.remember_master_pair_key("AA:AA:AA:AA:AA:AA", "aa" * 32)
        listed = self.store.machines()["AA:AA:AA:AA:AA:AA"]
        self.assertTrue(listed["paired"])
        self.assertNotIn("key", listed)
        self.assertNotIn("aa" * 32, json.dumps(listed))

    def test_forgetting_one_leaves_the_others_and_moves_the_cursor(self):
        self.store.add("AA:AA:AA:AA:AA:AA")
        self.store.add("BB:BB:BB:BB:BB:BB")
        self.store.select("AA:AA:AA:AA:AA:AA")
        self.store.forget("AA:AA:AA:AA:AA:AA")
        self.assertEqual(["BB:BB:BB:BB:BB:BB"], list(self.store.machines()))
        self.assertEqual("BB:BB:BB:BB:BB:BB", self.store.selected())

    def test_addresses_are_the_same_machine_whatever_their_case(self):
        self.store.add("aa:aa:aa:aa:aa:aa")
        self.store.remember_master_pair_key("AA:AA:AA:AA:AA:AA", "aa" * 32)
        self.assertEqual(1, len(self.store.machines()))
        self.assertEqual("aa" * 32, self.store.master_pair_key("aa:aa:aa:aa:aa:aa"))

    def test_the_file_is_not_readable_by_anyone_else(self):
        self.store.add("AA:AA:AA:AA:AA:AA")
        self.assertEqual(0o600, os.stat(self.path).st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
