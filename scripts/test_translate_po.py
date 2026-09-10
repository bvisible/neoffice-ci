"""The translator must never write a translation identical to its source.

Frappe merges every installed app's catalogue into ONE flat dict, in
installation order, last app winning. So a catalogue answering "System Manager"
for "System Manager" does not say nothing: it ERASES the real translation
another app ships, on every screen of the site.

Measured on the dev instance on 2026-09-10: 1345 msgids where two installed apps
disagree, 43 of them decided by an identity -- "System Manager" (disputed by 29
apps), "ID", "Stock Entry", "Stock Manager", "POS"… all read in English by the
whole fleet because one catalogue said the English word back
(neoffice-maintenance#335).

An empty msgstr says the same thing at no cost: the screen falls back to the
source text either way, and another app's translation is free to apply.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import translate_po  # noqa: E402


def _answer(pairs):
    """Fake the model: return this exact JSON array."""
    import json

    return json.dumps([{"i": i, "t": t} for i, t in pairs])


class TestIdentityIsRefused(unittest.TestCase):
    def _translate(self, items, answers):
        with patch.object(translate_po, "_claude", return_value=_answer(answers)):
            return translate_po.translate_batch(items, "fr", "sonnet")

    def test_an_identity_is_dropped(self):
        got = self._translate(["System Manager"], [(0, "System Manager")])
        self.assertEqual(got, {}, "an identity must never be written")

    def test_an_identity_differing_only_by_surrounding_space_is_dropped(self):
        got = self._translate(["Notes"], [(0, "  Notes  ")])
        self.assertEqual(got, {})

    def test_a_real_translation_still_passes(self):
        got = self._translate(["Stock Entry"], [(0, "Écriture de stock")])
        self.assertEqual(got, {0: "Écriture de stock"})

    def test_a_case_change_is_a_real_translation(self):
        """`email` -> `E-mail` differs, and differing is the whole point."""
        got = self._translate(["email"], [(0, "E-mail")])
        self.assertEqual(got, {0: "E-mail"})

    def test_the_batch_keeps_the_good_ones_around_a_refused_identity(self):
        got = self._translate(
            ["Stock Entry", "System Manager", "Table"],
            [(0, "Écriture de stock"), (1, "System Manager"), (2, "Tableau")],
        )
        self.assertEqual(got, {0: "Écriture de stock", 2: "Tableau"})

    def test_a_placeholder_mismatch_is_still_refused(self):
        """The guard that already existed must survive the new one."""
        got = self._translate(["Hello {0}"], [(0, "Bonjour")])
        self.assertEqual(got, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
