"""The light check of a translations-only push reads each catalogue as Frappe does: leniently, with Babel.

Run: python3 -m unittest scripts/test_check_po_reads.py (needs Babel; the Self tests install it).
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import check_po_reads  # noqa: E402

try:
    import babel  # noqa: F401
except ImportError:  # pragma: no cover — the Self tests install it
    babel = None

HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'


@unittest.skipIf(babel is None, "Babel is not installed")
class TestACatalogueReadsAsFrappeReadsIt(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, content: bytes):
        path = Path(self.dir.name) / name
        path.write_bytes(content)
        return str(path)

    def run_check(self, *paths):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_po_reads.main(list(paths))
        return code, out.getvalue()

    def test_a_good_catalogue_passes(self):
        po = self.write("fr.po", (HEADER + 'msgid "Hello"\nmsgstr "Bonjour"\n').encode())
        self.assertEqual(self.run_check(po)[0], 0)

    def test_what_msgfmt_refuses_and_frappe_serves_passes(self):
        # A duplicated entry and a placeholder the translation drops: `msgfmt --check` stops on both,
        # Babel (and so `bench compile-po-to-mo`) compiles the catalogue.
        po = self.write(
            "fr.po",
            (
                HEADER
                + 'msgid "Hello"\nmsgstr "Bonjour"\n\nmsgid "Hello"\nmsgstr "Salut"\n\n'
                + '#, python-format\nmsgid "%(n)s items"\nmsgstr "articles"\n'
            ).encode(),
        )
        self.assertEqual(self.run_check(po)[0], 0)

    def test_a_catalogue_frappe_cannot_compile_fails_and_is_named(self):
        bad = self.write("de.po", (HEADER + 'msgid "Hello"\nmsgstr "Hallo ').encode() + b"\xff\xfe\"\n")
        good = self.write("fr.po", (HEADER + 'msgid "Hello"\nmsgstr "Bonjour"\n').encode())
        code, out = self.run_check(good, bad)
        self.assertEqual(code, 1)
        self.assertIn("de.po", out)
        self.assertIn("UnicodeDecodeError", out)
        self.assertIn("1 refused", out)

    def test_a_catalogue_missing_at_this_commit_fails(self):
        self.assertEqual(self.run_check(str(Path(self.dir.name) / "gone.po"))[0], 1)

    def test_no_catalogue_passes(self):
        self.assertEqual(self.run_check()[0], 0)


if __name__ == "__main__":
    unittest.main()
