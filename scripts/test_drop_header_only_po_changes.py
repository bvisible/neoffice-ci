"""The nightly translation commits a PO only when something more than its header date changed.

On 03.11 four apps had received one commit a night since 21.10 whose only change was the `POT-Creation-Date` line of
`locale/fr.po` (erpnext_quota, telephony, weglot_integration, neoffice_custom_fields): the fleet looked behind GitHub
every morning (neoffice-maintenance#2179). Each case runs in a throwaway git repository.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "drop_header_only_po_changes.py"

PO = """msgid ""
msgstr ""
"Project-Id-Version: Telephony VERSION\\n"
"POT-Creation-Date: {date}\\n"
"PO-Revision-Date: 2026-10-20 03:34+0000\\n"
"Language: fr\\n"

msgid "Call"
msgstr "{call}"
"""


class Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.po = self.root / "telephony" / "locale" / "fr.po"
        self.po.parent.mkdir(parents=True)
        self.po.write_text(PO.format(date="2026-11-02 03:34+0000", call="Appel"))
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def night(self, **po):
        """What the workflow does: rewrite the PO, stage it, run the script; return what stays staged."""
        self.po.write_text(PO.format(**po))
        self.git("add", "telephony/locale/fr.po")
        done = subprocess.run([sys.executable, str(SCRIPT)], cwd=self.root, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return [p for p in self.git("diff", "--cached", "--name-only").splitlines() if p]


class TestTheNightlyCommit(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.dir.cleanup)

    def test_a_new_header_date_alone_is_not_committed(self):
        staged = self.repo.night(date="2026-11-03 03:34+0000", call="Appel")
        self.assertEqual(staged, [])
        self.assertIn("2026-11-02 03:34", self.repo.po.read_text(), "the file is put back as committed")

    def test_a_new_translation_is_committed_with_its_date(self):
        staged = self.repo.night(date="2026-11-03 03:34+0000", call="Appel téléphonique")
        self.assertEqual(staged, ["telephony/locale/fr.po"])

    def test_a_new_po_file_is_committed(self):
        new = self.repo.root / "telephony" / "locale" / "de.po"
        new.write_text(PO.format(date="2026-11-03 03:34+0000", call="Anruf"))
        self.repo.git("add", str(new.relative_to(self.repo.root)))
        subprocess.run([sys.executable, str(SCRIPT)], cwd=self.repo.root, check=True, capture_output=True)
        self.assertIn("telephony/locale/de.po", self.repo.git("diff", "--cached", "--name-only"))


if __name__ == "__main__":
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    unittest.main()
