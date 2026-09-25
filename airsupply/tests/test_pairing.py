"""The pairing key, which the add-on kept losing.

libairmini answers `airmini_pair` with the key as bare hex rather than as
JSON, so session._decode cannot parse it and hands back the text. The
controller used to look only for a {"masterPairKey": ...} object, found a
string, and threw the key away -- so every connection asked for the PIN again
and the reconnect-on-start path could never run. Seen on hardware 2026-09-25.
"""

import unittest

from airsupply.controller import master_pair_key

KEY = "9f3c1d77a4e28b06c5518af2d93e7b41c0a6de85f27b34199cd6e0ab7f452d38"


class MasterPairKey(unittest.TestCase):
    def test_bare_hex_is_the_key(self):
        self.assertEqual(KEY, master_pair_key(KEY))

    def test_surrounding_whitespace_does_not_hide_it(self):
        self.assertEqual(KEY, master_pair_key(f"  {KEY}\n"))

    def test_the_documented_object_shape_still_works(self):
        self.assertEqual(KEY, master_pair_key({"masterPairKey": KEY}))

    def test_anything_that_is_not_a_key_is_refused(self):
        # Storing one of these means a PIN-free reconnect that fails every
        # night afterwards, and looks like the machine's fault.
        for result in ("", "OK", "session-open", "not hex at all", "abc123", None,
                       {"state": "paired"}, {"masterPairKey": None}, ["..."], 42):
            self.assertIsNone(master_pair_key(result), repr(result))


if __name__ == "__main__":
    unittest.main()
