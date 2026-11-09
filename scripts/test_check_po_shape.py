"""The shape check refuses an entry written twice and two translations glued in one msgstr (#1953)."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_po_shape  # noqa: E402

HEADER = 'msgid ""\nmsgstr ""\n"Language: fr\\n"\n\n'
CLEAN = (
    HEADER
    + 'msgid "Save"\nmsgstr "Enregistrer"\n\n'
    + 'msgctxt "Gym"\nmsgid "Save"\nmsgstr "Sauver"\n\n'  # the same word with a context is another entry
    + 'msgid "A long one"\nmsgstr ""\n"Une traduction que Babel a repliée "\n"sur deux lignes."\n\n'
    + 'msgid "Two lines"\nmsgstr "Ligne un\\n"\n"Ligne deux"\n\n'  # a text of several lines is no glue
    + 'msgid "One file"\nmsgid_plural "{0} files"\nmsgstr[0] "Un fichier"\nmsgstr[1] "{0} fichiers"\n\n'
    + '#~ msgid "Gone"\n#~ msgstr "Parti"\n'
)
TWICE = CLEAN + '\nmsgid "Save"\nmsgstr "Sauvegarder"\n'
GLUED = CLEAN + '\nmsgid "Cancel"\nmsgstr "Annuler."\n"Abandonner."\n'


def _problems(text):
    return [(kind, ctx, msgid) for _line, kind, ctx, msgid, _what in check_po_shape.shape_problems(text)]


class TestShapeProblems(unittest.TestCase):
    def test_a_clean_catalogue_has_none(self):
        self.assertEqual(_problems(CLEAN), [])

    def test_an_entry_written_twice_is_named(self):
        self.assertEqual(_problems(TWICE), [("twice", "", "Save")])

    def test_the_same_word_with_another_context_is_not_a_second_copy(self):
        self.assertEqual(_problems(CLEAN + '\nmsgctxt "Shop"\nmsgid "Save"\nmsgstr "Garder"\n'), [])
        self.assertEqual(
            _problems(CLEAN + '\nmsgctxt "Gym"\nmsgid "Save"\nmsgstr "Garder"\n'), [("twice", "Gym", "Save")]
        )

    def test_two_translations_glued_in_one_msgstr_are_named(self):
        self.assertEqual(_problems(GLUED), [("glued", "", "Cancel")])

    def test_a_glue_in_an_entry_with_a_context_names_the_context(self):
        text = CLEAN + '\nmsgctxt "Gym"\nmsgid "Cancel"\nmsgstr "Annuler."\n"Abandonner."\n'
        self.assertEqual(_problems(text), [("glued", "Gym", "Cancel")])

    def test_a_wrapped_msgstr_that_starts_empty_and_a_several_lines_text_are_no_glue(self):
        self.assertEqual([p for p in _problems(CLEAN) if p[0] == "glued"], [])


class TestTheCommandLine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _po(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_po_shape.main(list(argv))
        return code, out.getvalue()

    def test_a_clean_catalogue_passes(self):
        code, output = self._run(self._po("fr.po", CLEAN))
        self.assertEqual(code, 0, output)

    def test_an_entry_written_twice_fails_the_run(self):
        code, output = self._run(self._po("fr.po", TWICE))
        self.assertEqual(code, 1)
        self.assertIn("written twice", output)
        self.assertIn("'Save'", output)

    def test_glued_translations_fail_the_run(self):
        code, output = self._run(self._po("fr.po", GLUED))
        self.assertEqual(code, 1)
        self.assertIn("glued", output)

    def test_damage_the_committed_file_already_had_is_a_warning_not_a_failure(self):
        committed = self._po("committed.po", TWICE)
        night = self._po("night.po", TWICE)
        code, output = self._run("--before", committed, night)
        self.assertEqual(code, 0, output)
        self.assertIn("::warning::", output)
        self.assertIn("already so in the committed file", output)

    def test_damage_the_night_brings_fails_even_when_the_committed_file_had_other_damage(self):
        committed = self._po("committed.po", TWICE)
        night = self._po("night.po", TWICE + '\nmsgid "Cancel"\nmsgstr "Annuler."\n"Abandonner."\n')
        code, output = self._run("--before", committed, night)
        self.assertEqual(code, 1)
        self.assertIn("::error::", output)
        self.assertIn("Cancel", output)

    def test_a_clean_night_after_a_clean_commit_passes(self):
        code, _ = self._run("--before", self._po("committed.po", CLEAN), self._po("night.po", CLEAN))
        self.assertEqual(code, 0)

    def test_without_a_committed_file_every_problem_fails(self):
        code, _ = self._run("--before", str(self.dir / "missing.po"), self._po("night.po", TWICE))
        self.assertEqual(code, 1)

    def test_as_a_real_command(self):
        script = Path(__file__).resolve().parent / "check_po_shape.py"
        bad = self._po("bad.po", GLUED)
        run = subprocess.run([sys.executable, str(script), bad], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 1, run.stdout)
        run = subprocess.run([sys.executable, str(script), self._po("ok.po", CLEAN)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
