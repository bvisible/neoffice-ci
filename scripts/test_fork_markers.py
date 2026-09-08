"""fork_markers.py check, on a throwaway repository. Run: python3 -m unittest scripts/test_fork_markers.py"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import fork_markers  # noqa: E402

BASE = '''import frappe


def price(item):
    """The price of an item.

    Looked up on the selling list.
    """
    query = """
        select rate from price
        where item = %s
    """
    return frappe.db.sql(query, item)
'''


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.commit(BASE)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, content):
        with open(os.path.join(self.repo, "mod.py"), "w") as f:
            f.write(content)
        self.git("add", "mod.py")
        self.git("commit", "-q", "-m", "step")
        return self.git("rev-parse", "HEAD")

    def unmarked(self, before, after):
        return fork_markers.check(self.repo, before, after, verbose=False)

    def test_a_docstring_edit_needs_no_marker(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("Looked up on the selling list.", "Looked up on the selling list, then on the default one."))
        self.assertEqual(self.unmarked(base, head), [])

    def test_a_docstring_line_removed_needs_no_marker(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("\n    Looked up on the selling list.\n", "\n"))
        self.assertEqual(self.unmarked(base, head), [])

    def test_a_code_change_without_a_marker_is_flagged(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("return frappe.db.sql(query, item)", "return frappe.db.sql(query, item, as_dict=True)"))
        self.assertEqual([u["kind"] for u in self.unmarked(base, head)], ["modified"])

    def test_a_string_that_is_code_stays_subject_to_the_rule(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("where item = %s", "where item = %s and disabled = 0"))
        self.assertEqual(len(self.unmarked(base, head)), 1, "an SQL query is not a docstring")

    def test_a_marked_code_change_passes(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("    return frappe.db.sql(query, item)", "    # //// Neoffice — rows as dicts, the caller reads .rate\n    return frappe.db.sql(query, item, as_dict=True)"))
        self.assertEqual(self.unmarked(base, head), [])

    def test_a_docstring_edit_next_to_a_code_change_flags_only_the_code(self):
        base = self.git("rev-parse", "HEAD")
        head = self.commit(BASE.replace("The price of an item.", "The price of one item.").replace("return frappe.db.sql(query, item)", "return frappe.db.sql(query, item) or []"))
        found = self.unmarked(base, head)
        self.assertEqual(len(found), 1)
        self.assertIn("or []", found[0]["snippet"][0])


if __name__ == "__main__":
    unittest.main()
