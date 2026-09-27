"""A rebuilt catalogue must not lose a translation the code still uses.

`bench update-po-files` keeps only what Frappe's extractor finds. It misses a `__()` in an
HTML attribute of a JS template, a label translated through a variable, a custom field
description... and each night the Translate workflow deleted those translations while
the screens still showed the strings (neoffice-maintenance#866).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import polib  # noqa: E402

import keep_used_translations as kut  # noqa: E402


def _po(path, entries, obsolete=()):
    po = polib.POFile()
    po.metadata = {"Language": "fr", "Content-Type": "text/plain; charset=UTF-8"}
    for msgid, msgstr in entries:
        po.append(polib.POEntry(msgid=msgid, msgstr=msgstr))
    for msgid, msgstr in obsolete:
        po.append(polib.POEntry(msgid=msgid, msgstr=msgstr, obsolete=True))
    po.save(path)


class TestKeepUsedTranslations(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.src = root / "app"
        (self.src / "app" / "public" / "js").mkdir(parents=True)
        (self.src / "app" / "locale").mkdir(parents=True)
        (self.src / "app" / "public" / "js" / "access.js").write_text(
            'const html = `<input placeholder="${__("Search a role")}">`;\n'
        )
        (self.src / "app" / "labels.py").write_text('LABELS = {"gym": "Fitness"}\n')
        # A catalogue the source no longer mentions: its line must not come back.
        (self.src / "app" / "locale" / "fr.po").write_text('msgid "Gone"\nmsgstr "Parti"\n')
        self.before = str(root / "before.po")
        self.after = str(root / "after.po")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_translation_the_code_still_uses_is_put_back(self):
        _po(self.before, [("Search a role", "Chercher un rôle"), ("Fitness", "Fitness FR"), ("Save", "Enregistrer")])
        _po(self.after, [("Save", "Enregistrer")])
        kept = kut.keep(self.before, self.after, str(self.src))
        self.assertEqual(sorted(kept), ["Fitness", "Search a role"])
        after = {e.msgid: e.msgstr for e in polib.pofile(self.after)}
        self.assertEqual(after["Search a role"], "Chercher un rôle")
        self.assertEqual(after["Fitness"], "Fitness FR")

    def test_a_translation_gone_from_the_code_stays_gone(self):
        _po(self.before, [("Gone", "Parti"), ("Save", "Enregistrer")])
        _po(self.after, [("Save", "Enregistrer")])
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), [])
        self.assertNotIn("Gone", {e.msgid for e in polib.pofile(self.after)})

    def test_an_obsolete_copy_gives_way_to_the_live_entry(self):
        _po(self.before, [("Search a role", "Chercher un rôle")])
        _po(self.after, [], obsolete=[("Search a role", "Chercher un rôle")])
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), ["Search a role"])
        entries = [e for e in polib.pofile(self.after) if e.msgid == "Search a role"]
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].obsolete)

    def test_an_untranslated_entry_is_not_put_back(self):
        _po(self.before, [("Search a role", "")])
        _po(self.after, [])
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), [])

    def test_nothing_changes_when_nothing_was_dropped(self):
        _po(self.before, [("Save", "Enregistrer")])
        _po(self.after, [("Save", "Enregistrer")])
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), [])


if __name__ == "__main__":
    unittest.main()
