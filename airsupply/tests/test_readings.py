"""How a real AirMini's answers come out on the page.

Every value here was read off the machine on 2026-09-25 and is recorded in
docs/verify.md. The point of the file is that these four shapes -- and the run
meters especially -- are what the page has to make readable, so a change to
api.fields() that regresses them fails here rather than on a nightstand.
"""

import unittest

from airsupply import api

SETTINGS = {
    "ActiveTherapyProfile": "AutoSetProfile",
    "FGState": "Standby",
    "FeatureProfiles": {
        "AutoRampFeature": {"RampEnable": "Auto", "RampTime": 10},
        "ComfortFeature": {"AutoSetComfort": "Off"},
        "EprFeature": {"EprEnable": "On", "EprEnablePatientAccess": "On",
                       "EprPressure": 1, "EprType": "Ramp"},
        "SmartStartStopFeature": {"SmartStart": "On", "SmartStop": "On"},
    },
    "TherapyProfiles": {
        "AutoSetForHerProfile": {"MaxPressure": 20.0, "MinPressure": 5.0,
                                 "StartPressure": 5.0, "TherapyMode": "HerAuto"},
        "AutoSetProfile": {"MaxPressure": 20.0, "MinPressure": 5.0,
                           "StartPressure": 5.0, "TherapyMode": "AutoSet"},
        "CpapProfile": {"SetPressure": 10.0, "StartPressure": 5.0, "TherapyMode": "CPAP"},
    },
}

METRICS = {
    "MachineMetrics": {
        "Attributes": {"ReportDateTime": "2026-09-25T12:13:15.798Z"},
        "LastEraseDataDateTime": None,
        "LastTherapyUseDateTime": "2026-09-25T06:26:51.000Z",
        "MachineRunMeter": "PT2591392S",
        "MotorRunMeter": "PT2591392S",
        "MotorRunSinceLastServiceMeter": "PT2591392S",
        "TherapyRunMeter": "PT2589645S",
    }
}


def rows(value):
    return {row["label"]: row["value"] for row in api.fields(value)}


class RunMeters(unittest.TestCase):
    def test_a_run_meter_reads_as_hours(self):
        # 2591392 seconds is what the machine says; nobody wants it in seconds.
        self.assertEqual("719 h 49 min", api.scalar("PT2591392S"))
        self.assertEqual("719 h 20 min", api.scalar("PT2589645S"))

    def test_the_other_iso_duration_shapes_still_add_up(self):
        self.assertEqual("45 min", api.scalar("PT45M"))
        self.assertEqual("1 h 30 min", api.scalar("PT1H30M"))
        self.assertEqual("25 h 00 min", api.scalar("P1DT1H"))

    def test_words_that_are_not_durations_are_left_alone(self):
        for word in ("Standby", "AutoSet", "CPAP", "Ramp", "On", "PT"):
            self.assertEqual(word, api.scalar(word), word)


class Instants(unittest.TestCase):
    def test_a_timestamp_reads_as_a_date(self):
        self.assertRegex(api.scalar("2026-09-25T06:26:51.000Z"), r"^2[45] Sep 2026, \d\d:\d\d$")

    def test_a_version_string_is_not_a_timestamp(self):
        self.assertEqual("1.0.0.270", api.scalar("1.0.0.270"))
        self.assertEqual("SW03900.01.4.0.3.50927", api.scalar("SW03900.01.4.0.3.50927"))


class Settings(unittest.TestCase):
    def test_a_name_three_profiles_share_takes_the_profile_with_it(self):
        labels = rows(SETTINGS)
        self.assertEqual("20", labels["Auto set profile max pressure"])
        self.assertEqual("5", labels["Auto set for her profile min pressure"])
        self.assertEqual("5", labels["Cpap profile start pressure"])

    def test_a_name_only_one_profile_has_stays_short(self):
        # SetPressure belongs to CpapProfile alone, so nothing has to be said
        # about which profile it came from. Each label is the shortest name
        # that is still unambiguous, which is why some carry a profile and
        # their neighbours do not.
        self.assertEqual("10", rows(SETTINGS)["Set pressure"])

    def test_a_setting_with_one_home_keeps_the_short_name(self):
        labels = rows(SETTINGS)
        self.assertEqual("Standby", labels["FG state"])
        self.assertEqual("AutoSetProfile", labels["Active therapy profile"])
        self.assertEqual("10", labels["Ramp time"])


class Metrics(unittest.TestCase):
    def test_the_meters_and_the_dates_are_both_readable(self):
        labels = rows(METRICS)
        self.assertEqual("719 h 49 min", labels["Machine run meter"])
        self.assertEqual("719 h 20 min", labels["Therapy run meter"])
        self.assertEqual("not set", labels["Last erase data date time"])
        self.assertRegex(labels["Last therapy use date time"], r"^2[45] Sep 2026, ")


if __name__ == "__main__":
    unittest.main()


VERSION = {
    "BluetoothModule": {
        "IdentificationProfiles": {
            "Hardware": {"HardwareIdentifier": "(90)R380-XXXX(91)8.0(21)XXXXXXXXX"},
            "Product": {"UniversalIdentifier": "cc05ae8b-0000-0000-0000-000000000000"},
            "Software": {"ApplicationIdentifier": "ST266.1.1.3.199.2"},
        }
    },
    "FlowGenerator": {
        "IdentificationProfiles": {
            "Hardware": {"HardwareIdentifier": "(90)R380-XXXX(91)8.0(21)XXXXXXXXX"},
            "Product": {"UniversalIdentifier": "cc05ae8b-0000-0000-0000-000000000000"},
            "Software": {
                "ApplicationIdentifier": "SW03900.01.4.0.3.50927",
                "BootloaderIdentifier": "SW03901.00.3.0.0.48255",
            },
        },
        "RPC": {"Get": "1.0", "GetVersion": "2.0"},
    },
}


class Version(unittest.TestCase):
    """Two subsystems reporting the same field names, three levels down."""

    def test_the_part_of_the_machine_is_what_tells_them_apart(self):
        labels = rows(VERSION)
        self.assertEqual("SW03900.01.4.0.3.50927", labels["Flow generator application identifier"])
        self.assertEqual("ST266.1.1.3.199.2", labels["Bluetooth module application identifier"])

    def test_no_two_rows_share_a_label(self):
        # The bug this replaced: both application identifiers came out as
        # "Software application identifier", so the page showed two different
        # values under one name and no way to tell which machine part was which.
        labels = [row["label"] for row in api.fields(VERSION)]
        self.assertEqual(sorted(labels), sorted(set(labels)))

    def test_a_label_does_not_say_the_same_word_twice(self):
        # "Hardware" + "HardwareIdentifier" used to read "Hardware hardware
        # identifier", because the parent was assumed to be what distinguishes.
        self.assertIn("Flow generator hardware identifier", rows(VERSION))

    def test_a_name_only_one_subsystem_uses_stays_short(self):
        self.assertEqual("SW03901.00.3.0.0.48255", rows(VERSION)["Bootloader identifier"])
