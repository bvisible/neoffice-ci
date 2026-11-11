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
from unittest import mock

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

    # The rebuilt file as `bench update-po-files` writes it: no wrapping, so a long msgstr stays on one line.
    REBUILT = (
        'msgid ""\n'
        'msgstr ""\n'
        '"Language: fr\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        "\n"
        "#: app/page.js:12\n"
        'msgid "Save"\n'
        'msgstr "Enregistrer"\n'
        "\n"
        "#: app/page.js:40\n"
        'msgid "The roles of a person decide what the person sees in every space of the company, and what not"\n'
        'msgstr "Les rôles d\'une personne décident de ce qu\'elle voit dans chaque espace de l\'entreprise, et de ce qu\'elle ne voit pas"\n'
    )
    COMMITTED = (
        'msgid ""\n'
        'msgstr ""\n'
        '"Language: fr\\n"\n'
        "\n"
        "#: app/public/js/access.js:1\n"
        'msgid "Search a role"\n'
        'msgstr "Chercher un rôle"\n'
    )

    def test_the_rebuilt_file_keeps_every_line_and_gets_the_committed_entry_as_it_was(self):
        # maintenance#1953: polib's save re-wrapped every long msgstr of the file to put one entry back
        Path(self.after).write_text(self.REBUILT, encoding="utf-8")
        Path(self.before).write_text(self.COMMITTED, encoding="utf-8")
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), ["Search a role"])
        written = Path(self.after).read_text(encoding="utf-8")
        self.assertTrue(written.startswith(self.REBUILT), "a line of the rebuilt file moved")
        self.assertEqual(
            written[len(self.REBUILT):],
            '\n#: app/public/js/access.js:1\nmsgid "Search a role"\nmsgstr "Chercher un rôle"\n',
        )

    def test_an_obsolete_copy_leaves_by_its_own_lines(self):
        Path(self.after).write_text(
            self.REBUILT + '\n#~ msgid "Search a role"\n#~ msgstr "Chercher un rôle"\n', encoding="utf-8"
        )
        Path(self.before).write_text(self.COMMITTED, encoding="utf-8")
        self.assertEqual(kut.keep(self.before, self.after, str(self.src)), ["Search a role"])
        written = Path(self.after).read_text(encoding="utf-8")
        self.assertTrue(written.startswith(self.REBUILT))
        self.assertNotIn("#~", written)
        entries = [e for e in polib.pofile(self.after) if e.msgid == "Search a role"]
        self.assertEqual([(e.obsolete, e.msgstr) for e in entries], [(False, "Chercher un rôle")])

    def test_nothing_is_written_when_the_reread_would_differ(self):
        Path(self.after).write_text(self.REBUILT, encoding="utf-8")
        Path(self.before).write_text(self.COMMITTED, encoding="utf-8")
        real = kut._state
        reads = iter([real, lambda text: ({"moved": 1}, {}), real])  # the re-read of the new text differs
        with mock.patch.object(kut, "_state", side_effect=lambda text: next(reads)(text)):
            with self.assertRaises(kut.WriteRefused):
                kut.keep(self.before, self.after, str(self.src))
        self.assertEqual(Path(self.after).read_text(encoding="utf-8"), self.REBUILT)


if __name__ == "__main__":
    unittest.main()
