"""The pairing key, which the add-on kept losing.

libairmini builds the pairing result with snprintf into a `char res[160]`:

    {"masterPairKey":"<64 hex>","sessionKey":"<64 hex>"}

which needs 165 bytes. So 159 characters arrive, the closing quote and brace
are gone, it does not parse as JSON, and session._decode passes the text
through. The key is unhurt -- it ends at character 82 -- but the controller
was looking for an object, finding a string, and dropping it, so every
connection asked for the PIN again and the reconnect-on-start path could
never run at all. Seen on hardware 2026-09-25, twice.
"""

import unittest

from airsupply.controller import master_pair_key

KEY = "9f3c1d77a4e28b06c5518af2d93e7b41c0a6de85f27b34199cd6e0ab7f452d38"


SESSION = "1b4e28ba2fa1c0d3e5f6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192"
WHOLE = '{"masterPairKey":"' + KEY + '","sessionKey":"' + SESSION + '"}'
TRUNCATED = WHOLE[:159]


class MasterPairKey(unittest.TestCase):
    def test_the_truncated_result_a_real_machine_sends(self):
        # What actually arrives: 164 characters of JSON through a 160-byte
        # buffer. The key is intact; the JSON is not.
        self.assertEqual(164, len(WHOLE))
        self.assertEqual(159, len(TRUNCATED))
        self.assertEqual(KEY, master_pair_key(TRUNCATED))

    def test_the_whole_result_an_upstream_fix_would_send(self):
        self.assertEqual(KEY, master_pair_key(WHOLE))

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
                       {"state": "paired"}, {"masterPairKey": None}, ["..."], 42,
                       '{"message":"bad passkey"}',
                       '{"masterPairKey":"tooshort"}'):
            self.assertIsNone(master_pair_key(result), repr(result))


if __name__ == "__main__":
    unittest.main()
