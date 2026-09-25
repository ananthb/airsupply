"""Check test_api's contract against the Elm decoders it claims to mirror.

test_api.py names the fields the page decodes. That list is only worth having
if it cannot drift from web/src/Main.elm, so this reads the decoders out of the
Elm source and compares them.

Skipped when the Elm source is not there, which is the case inside the image:
the container carries the compiled page, not its source, and the image build
runs these tests too.
"""

import pathlib
import re
import unittest

import test_api

MAIN = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "Main.elm"

DECODERS = {
    "stateDecoder": test_api.STATE,
    "machineDecoder": test_api.MACHINE,
    "readingDecoder": test_api.READING,
    "sectionDecoder": test_api.SECTION,
    "fieldDecoder": test_api.FIELD,
    "questionDecoder": test_api.QUESTION,
    "lineDecoder": test_api.LINE,
}


@unittest.skipUnless(MAIN.is_file(), "the Elm source is not in the image")
class Decoders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elm = MAIN.read_text(encoding="utf-8")

    def test_each_decoder_asks_for_exactly_the_documented_fields(self):
        for name, expected in DECODERS.items():
            with self.subTest(decoder=name):
                match = re.search(rf"^{name} :.*?\n(.*?)(?=\n\n\n)", self.elm, re.S | re.M)
                self.assertIsNotNone(match, f"{name} is gone from Main.elm")
                found = set(re.findall(r'D\.field "([^"]+)"', match.group(1)))
                self.assertEqual(set(expected), found)


if __name__ == "__main__":
    unittest.main()
