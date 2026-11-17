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
NATURES = 'NATURES = ("repas", "restaurant", "verpflegung", "mahlzeit", "pasto")\n'


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
        return lists.lists_of(self.root, self.configure())[0]

    def refused(self):
        config = self.configure()
        return lists.judge(*lists.lists_of(self.root, config), config)

    def run_script(self, *args):
        self.configure()
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args], capture_output=True, text=True, timeout=60
        )

    def declare(self, entry, **fields):
        """Declares a list the check found (or printed with --declare), accepted unless the fields say otherwise."""
        declaration = {
            "path": entry["path"],
            "name": entry["name"],
            "kind": "anchor",
            "why": "finds the line of the total printed on a receipt",
            "members": entry["members"],
            "accepted_by": "the owner",
            "accepted_on": "2026-11-18",
        }
        declaration.update(fields)
        self.declared.append({key: value for key, value in declaration.items() if value is not None})

    def inherit(self, entry, **fields):
        self.declare(
            entry, accepted_by=None, accepted_on=None, **{"inherited_on": "2026-11-17", "until": "#312", **fields}
        )


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

    def test_a_text_of_several_lines_is_prose_not_a_pattern(self):
        self.write(
            "app/api/reading.py",
            '''
            PROMPT = """You read a document.
            "document_type": "facture|quittance|relevé"
            Keep every printed line."""
            ONE_LINE = "facture|quittance|relevé"
            ''',
        )
        self.assertEqual([entry["name"] for entry in self.found()], ["ONE_LINE"])

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

    def test_the_members_of_a_list_are_every_string_of_it_whatever_the_code_calls_technical(self):
        self.write("app/api/reading.py", NATURES)
        self.write("app/other.py", "restaurant = 1\n")
        (entry,) = self.found()
        self.assertNotIn("restaurant", entry["words"], "a name of the code is not an everyday word")
        self.assertEqual(entry["members"], ["repas", "restaurant", "verpflegung", "mahlzeit", "pasto"])


class TheRegistry(Repository):
    def setUp(self):
        super().setUp()
        self.write("app/api/reading.py", NATURES)
        (self.entry,) = self.found()

    def test_a_declared_list_passes_accepted_or_inherited(self):
        self.declare(self.entry)
        self.assertEqual(self.refused(), [])
        self.declared = []
        self.inherit(self.entry, kind="decides", why="chooses the account of a receipt by the name of the accounts")
        self.assertEqual(self.refused(), [])

    def test_one_more_word_in_a_declared_list_is_refused_and_named(self):
        self.inherit(self.entry)
        self.write("app/api/reading.py", NATURES.replace('"pasto"', '"pasto", "cantine"'))
        (refusal,) = self.refused()
        self.assertIn("the declared list grew by 1 word(s): cantine", refusal)
        self.assertIn("app/api/reading.py:1: NATURES", refusal)

    def test_a_new_literal_of_words_under_a_declared_name_is_growth_too(self):
        self.write("app/api/reading.py", "def nature(text):\n    return text.startswith('repas')\n")
        (entry,) = self.found()
        self.declared = []
        self.inherit(entry)
        self.assertEqual(self.refused(), [])
        self.write(
            "app/api/reading.py",
            "def nature(text):\n    if 'verpflegung' in text:\n        return 1\n    return text.startswith('repas')\n",
        )
        (refusal,) = self.refused()
        self.assertIn("grew by 1 word(s): verpflegung", refusal)

    def test_a_declared_list_may_lose_words_freely(self):
        self.inherit(self.entry)
        self.write("app/api/reading.py", 'NATURES = ("repas", "verpflegung", "mahlzeit")\n')
        self.assertEqual(self.refused(), [])
        self.write("app/api/reading.py", "NATURES = ()\n")
        self.assertEqual(self.refused(), [], "an emptied list keeps its name: nothing to refuse")

    def test_names_used_elsewhere_in_the_repository_never_make_a_declared_list_grow_or_vanish(self):
        self.inherit(self.entry)
        self.write("app/other.py", "def repas(verpflegung, mahlzeit, pasto):\n    return pasto\n")
        self.assertEqual(self.found(), [], "every word became a name of the code: no list is seen any more")
        self.assertEqual(self.refused(), [], "and the declaration is not refused for it")
        (self.root / "app/other.py").unlink()
        self.write("app/elsewhere.py", "x = 1\n")
        self.assertEqual(self.refused(), [], "the name stops being technical: the word was a declared member")
        self.assertIn("restaurant", self.entry["members"])

    def test_a_declaration_whose_file_or_name_is_gone_is_refused(self):
        self.inherit(self.entry)
        self.write("app/api/reading.py", 'KINDS_OF_MEAL = ("x",)\n')
        (refusal,) = self.refused()
        self.assertIn("is declared but this file or this name no longer exists", refusal)
        (self.root / "app/api/reading.py").unlink()
        (refusal,) = self.refused()
        self.assertIn("no longer exists", refusal)

    def test_a_declaration_says_what_why_its_members_and_who_accepted_it_or_when_it_was_found(self):
        for fields, expected in (
            ({"kind": "fine"}, "`kind` is one of"),
            ({"why": "it is fine"}, "`why` says in at least"),
            ({"members": []}, "`members` holds the words"),
            ({"members": None}, "`members` holds the words"),
            ({"accepted_by": " "}, "`accepted_by` names who accepted it"),
            ({"accepted_on": "yesterday"}, "`accepted_on` is the day"),
            ({"kind": "decides"}, "names in `until` the issue that removes it"),
            ({"accepted_by": None, "accepted_on": None, "inherited_on": "2026-11-17"}, "the issue that follows it"),
            ({"accepted_on": None, "inherited_on": "2026-11-17", "until": "#312"}, "inherited or accepted, not both"),
            ({"accepted_by": None, "inherited_on": "17.11.2026", "until": "#312"}, "`inherited_on` is the day"),
        ):
            with self.subTest(fields=fields):
                self.declared = []
                self.declare(self.entry, **fields)
                refused = self.refused()
                self.assertEqual(len([line for line in refused if "is incomplete" in line]), 1, refused)
                self.assertIn(expected, " ".join(refused))
        self.declared = []
        self.declare(self.entry, kind="decides", until="#2788")
        self.assertEqual(self.refused(), [])

    def test_a_path_exempt_from_the_key_line_is_still_judged_for_its_lists(self):
        self.watched[0]["key_line"] = False
        (refusal,) = self.refused()
        self.assertIn("a list of everyday words where the assistant decides", refusal)

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
        self.assertEqual(todo["members"][:3] + todo["members"][-2:], ["caisse", "caisses", "palette", "emballage", "verpackung"])
        self.inherit(todo, kind="decides", why="chooses the account of a part by the words of its lines")
        accepted = self.run_script()
        self.assertEqual((accepted.returncode, accepted.stdout), (0, ""))
        self.assertEqual(json.loads(self.run_script("--declare").stdout), [])

    def test_a_list_that_grew_comes_back_in_the_declarations_to_write(self):
        self.write("app/api/reading.py", NATURES)
        (todo,) = json.loads(self.run_script("--declare").stdout)
        self.inherit(todo)
        self.write("app/api/reading.py", NATURES.replace('"pasto"', '"pasto", "cantine"'))
        (again,) = json.loads(self.run_script("--declare").stdout)
        self.assertIn("cantine", again["members"])

    def test_the_inventory_lists_and_refuses_nothing(self):
        self.write("app/api/reading.py", STOPGAP)
        self.write("app/api/reading_two.py", NATURES)
        entry = next(found for found in self.found() if found["name"] == "NATURES")
        self.inherit(entry, kind="decides")
        done = self.run_script("--inventory")
        self.assertEqual(done.returncode, 0)
        self.assertIn("PART_KINDS [NOT DECLARED]", done.stdout)
        self.assertIn("NATURES [inherited decides]", done.stdout)
        self.assertIn("2 list(s) in the watched paths: 0 accepted, 1 inherited, 1 not declared", done.stdout)
        names = [found["name"] for found in json.loads(self.run_script("--inventory", "--json").stdout)]
        self.assertEqual(names, ["PART_KINDS", "NATURES"])

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
