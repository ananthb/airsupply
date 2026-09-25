"""Why the serial channel did not open, in a few words.

The reason is known at the point it happens and used to be thrown away: the
add-on answered "could not open the serial channel; see the log". Seen for
real on 2026-09-26, after switching the machine off.
"""

import unittest

from airsupply.bluez.spp import explain


class Error:
    def __init__(self, text):
        self.text = text


class Explain(unittest.TestCase):
    def test_a_machine_that_is_off_or_away_says_so(self):
        for text in ("br-connection-timeout", "br-connection-page-timeout", "Page Timeout"):
            said = explain(Error(text))
            self.assertEqual("No answer. Machine off or out of range.", said, text)

    def test_something_else_holding_the_machine_says_so(self):
        self.assertEqual("Machine in use elsewhere.", explain(Error("br-connection-already-connected")))

    def test_a_machine_with_no_serial_port_says_so(self):
        self.assertEqual("No serial port on the machine.",
                         explain(Error("br-connection-profile-unavailable")))

    def test_an_unknown_reason_is_passed_through_rather_than_hidden(self):
        said = explain(Error("br-connection-key-missing"))
        self.assertIn("br-connection-key-missing", said)

    def test_it_copes_with_something_that_is_not_a_dbus_error(self):
        self.assertIn("boom", explain(RuntimeError("boom")))

    def test_nothing_it_says_is_a_paragraph(self):
        # Messages are read by somebody standing next to a CPAP at midnight.
        for text in ("br-connection-timeout", "br-connection-already-connected",
                     "br-connection-profile-unavailable", "br-connection-in-progress",
                     "br-connection-refused"):
            said = explain(Error(text))
            self.assertLessEqual(len(said), 48, said)
            self.assertLessEqual(said.count("."), 2, said)


if __name__ == "__main__":
    unittest.main()
