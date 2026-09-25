"""Why the serial channel did not open, in words.

BlueZ says `br-connection-timeout`, which is accurate and tells somebody
looking at their own bedroom nothing. The add-on used to answer "could not
open the serial channel; see the log" and throw the reason away -- seen for
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
            self.assertIn("switched off, out of range", said, text)
            self.assertNotIn("br-connection", said)

    def test_something_else_holding_the_machine_says_so(self):
        self.assertIn("only one thing can be", explain(Error("br-connection-already-connected")))

    def test_a_machine_with_no_serial_port_suggests_the_fix(self):
        said = explain(Error("br-connection-profile-unavailable"))
        self.assertIn("factory reset", said)

    def test_an_unknown_reason_is_passed_through_rather_than_hidden(self):
        said = explain(Error("br-connection-key-missing"))
        self.assertIn("br-connection-key-missing", said)

    def test_it_copes_with_something_that_is_not_a_dbus_error(self):
        self.assertIn("boom", explain(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
