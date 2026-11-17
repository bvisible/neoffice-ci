#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_word_lists as lists  # noqa: E402

SCRIPT = Path(__file__).with_name("check_word_lists.py")
# The shape of the module that was pushed and removed on 17.11.2026: kinds, the words looked for in a line of the
# document, the words looked for in the name of an account.
STOPGAP = """
    PART_KINDS = {
        "packaging": (
            "Packaging and deposit",
            ("caisse", "caisses", "palette", "consigne", "pfand", "kiste", "imballaggio", "crate", "deposit"),
            ("emballage", "consigne", "verpackung", "pfand"),
        ),
    }

    def proposal(lines, accounts):
        return [account for account in accounts if any(word in account for word in PART_KINDS["packaging"][2])]
"""
OWN_WORDS = """
    POLICIES = ("skip", "once", "disable")
    STATES = ("Draft", "Paid", "Cancelled")
    FIELDS = {"supplier": 1, "amount": 2, "currency": 3}

    def skip(once, disable, supplier, amount, currency):
        return once
"""


class Repository(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="word_lists_"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)], check=False))
        self.watched = [{"path": "app/api/reading*.py", "why": "the reading of a scanned document"}]
        self.declared = []

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")

    def configure(self, **more):
        config = {"watched": self.watched, "declared": self.declared, **more}
        (self.root / lists.CONFIG).write_text(json.dumps(config), encoding="utf-8")
        return config

    def found(self):
        return lists.lists_of(self.root, self.configure())

    def refused(self):
        config = self.configure()
        return lists.judge(lists.lists_of(self.root, config), config)

    def run_script(self, *args):
        self.configure()
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args], capture_output=True, text=True, timeout=60
        )

    def declare(self, entry, **fields):
        declaration = {
            "path": entry["path"],
            "name": entry["name"],
            "fingerprint": entry["fingerprint"],
            "kind": "anchor",
            "why": "finds the line of the total printed on a receipt",
            "accepted_by": "the owner",
            "accepted_on": "2026-11-18",
        }
        declaration.update(fields)
        self.declared.append(declaration)


class WhatIsFound(Repository):
    def test_the_stopgap_of_17_11_is_one_list_with_all_its_words_and_its_line(self):
        self.write("app/api/reading_parts.py", STOPGAP)
        (entry,) = self.found()
        self.assertEqual((entry["path"], entry["name"], entry["line"]), ("app/api/reading_parts.py", "PART_KINDS", 4))
        self.assertEqual(entry["kinds"], ["word-collection"])
        for word in ("caisse", "palette", "pfand", "imballaggio", "emballage", "verpackung"):
            self.assertIn(word, entry["words"])
        (refusal,) = self.refused()
        self.assertIn("app/api/reading_parts.py:4: PART_KINDS", refusal)
        self.assertIn("a list of everyday words where the assistant decides", refusal)

    def test_the_codes_own_vocabulary_is_not_a_list(self):
        self.write("app/api/reading.py", OWN_WORDS)
        self.assertEqual(self.found(), [])

    def test_a_name_used_in_another_file_of_the_repository_is_technical_too(self):
        self.write("app/api/reading.py", 'KINDS = ("fuel", "lodging", "postage")\n')
        self.assertEqual(len(self.found()), 1)
        self.write("app/other.py", "def fuel(lodging, postage):\n    return lodging\n")
        self.assertEqual(self.found(), [])

    def test_a_table_a_pattern_and_a_single_word_looked_for_are_found(self):
        self.write(
            "app/api/reading.py",
            """
            import re

            ACCOUNT_OF = {"impôt": "8900", "taxation": "8900", "mazout": "6200"}
            CREDIT_NOTE = re.compile(r"\\b(note de cr[ée]dit|gutschrift|nota di credito)\\b", re.I)

            def is_vat(label, group):
                if "mwst" in label.lower():
                    return True
                return group.lower().startswith("sans tva")
            """,
        )
        kinds = {entry["name"]: entry["kinds"] for entry in self.found()}
        self.assertEqual(
            kinds, {"ACCOUNT_OF": ["word-table"], "CREDIT_NOTE": ["word-pattern"], "is_vat()": ["word-test"]}
        )
        tested = next(entry for entry in self.found() if entry["name"] == "is_vat()")
        self.assertEqual(tested["words"], ["mwst", "sans tva"])

    def test_membership_in_a_literal_a_docstring_and_a_comment_are_not_lists(self):
        self.write(
            "app/api/reading.py",
            '''
            def kind(value):
                """Words of a receipt: caisse, palette, consigne, pfand (caisse|palette|consigne)."""
                # caisse, palette, consigne
                return "caisse" in ("caisse", "x")
            ''',
        )
        self.assertEqual(self.found(), [])

    def test_javascript_lists_patterns_and_tests_are_found_and_comments_are_not(self):
        self.watched.append({"path": "app/public/js/window.js", "why": "the booking window"})
        self.write(
            "app/public/js/window.js",
            """
            // ["caisse", "palette", "consigne"] in a comment is nothing
            const _kw = ["note de crédit", "gutschrift", "nota di credito"];
            const isDeposit = /consigne|pfand|cauzione/i.test(text);
            if (group.toLowerCase().includes("sans tva")) { vat = ""; }
            """,
        )
        found = {(entry["name"], tuple(entry["kinds"])) for entry in self.found()}
        self.assertIn(("_kw", ("word-collection",)), found)
        self.assertIn(("isDeposit", ("word-pattern",)), found)
        self.assertTrue(any(kinds == ("word-test",) for _, kinds in found))
        self.assertEqual(len(found), 3)

    def test_outside_the_watched_paths_and_in_a_test_nothing_is_judged(self):
        self.write("app/api/chat_route.py", STOPGAP)
        self.write("app/api/reading_tests/test_reading.py", STOPGAP)
        self.write("app/tests/reading_x.py", STOPGAP)
        self.assertEqual(self.found(), [])


class TheRegistry(Repository):
    def setUp(self):
        super().setUp()
        self.write("app/api/reading.py", 'TOTAL_WORDS = ("rechnungsbetrag", "importo totale", "montant dû")\n')
        (self.entry,) = self.found()

    def test_a_declared_list_passes(self):
        self.declare(self.entry)
        self.assertEqual(self.refused(), [])

    def test_one_more_word_in_a_declared_list_is_refused_until_it_is_accepted_again(self):
        self.declare(self.entry)
        self.write("app/api/reading.py", 'TOTAL_WORDS = ("rechnungsbetrag", "importo totale", "montant dû", "solde")\n')
        (refusal,) = self.refused()
        self.assertIn("the declared list changed", refusal)
        self.assertIn("solde", refusal)

    def test_a_declaration_that_matches_nothing_any_more_is_refused(self):
        self.declare(self.entry)
        self.write("app/api/reading.py", "TOTAL_WORDS = ()\n")
        (refusal,) = self.refused()
        self.assertIn("declared but no such list exists any more", refusal)

    def test_a_declaration_says_what_why_who_and_when(self):
        for fields, expected in (
            ({"kind": "fine"}, "`kind` is one of"),
            ({"why": "it is fine"}, "`why` says in at least"),
            ({"accepted_by": " "}, "`accepted_by` names who accepted it"),
            ({"accepted_on": "yesterday"}, "`accepted_on` is the day"),
            ({"kind": "decides"}, "names in `until` the issue that removes it"),
        ):
            with self.subTest(fields=fields):
                self.declared = []
                self.declare(self.entry, **fields)
                (refusal,) = self.refused()
                self.assertIn(expected, refusal)
        self.declared = []
        self.declare(self.entry, kind="decides", until="#2788")
        self.assertEqual(self.refused(), [])

    def test_a_watched_pattern_says_why_it_is_watched(self):
        self.declare(self.entry)
        self.watched.append({"path": "app/api/other*.py", "why": ""})
        (refusal,) = self.refused()
        self.assertIn("does not say why it is watched", refusal)


class TheCommand(Repository):
    def test_without_the_configuration_nothing_is_watched(self):
        self.write("app/api/reading.py", STOPGAP)
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("nothing is watched", done.stdout)

    def test_a_list_that_is_not_declared_fails_the_run_and_the_declared_repository_passes(self):
        self.write("app/api/reading.py", STOPGAP)
        refused = self.run_script()
        self.assertEqual(refused.returncode, 1)
        self.assertIn("PART_KINDS", refused.stdout)
        (todo,) = json.loads(self.run_script("--declare").stdout)
        self.assertEqual((todo["kind"], todo["why"], todo["accepted_by"]), ("", "", ""))
        self.declare(todo, kind="support")
        accepted = self.run_script()
        self.assertEqual((accepted.returncode, accepted.stdout), (0, ""))
        self.assertEqual(json.loads(self.run_script("--declare").stdout), [])

    def test_the_inventory_lists_and_refuses_nothing(self):
        self.write("app/api/reading.py", STOPGAP)
        done = self.run_script("--inventory")
        self.assertEqual(done.returncode, 0)
        self.assertIn("NOT DECLARED", done.stdout)
        (entry,) = json.loads(self.run_script("--inventory", "--json").stdout)
        self.assertEqual(entry["name"], "PART_KINDS")

    def test_at_commit_time_it_judges_only_when_a_watched_file_or_the_configuration_is_given(self):
        self.write("app/api/reading.py", STOPGAP)
        self.assertEqual(self.run_script("app/other.py", "README.md").returncode, 0)
        self.assertEqual(self.run_script("app/api/reading.py").returncode, 1)
        self.assertEqual(self.run_script(lists.CONFIG).returncode, 1)

    def test_a_configuration_that_is_not_json_fails_the_run(self):
        (self.root / lists.CONFIG).write_text("{not json", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("is not valid JSON", done.stdout)


if __name__ == "__main__":
    unittest.main()
