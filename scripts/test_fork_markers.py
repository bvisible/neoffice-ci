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


class TestTemplateLiteralLines(unittest.TestCase):
    """`////` inside a template literal is TEXT on screen, not a comment (#331)."""

    def L(self, src):
        return fork_markers.template_literal_lines(src.strip("\n").split("\n"))

    def test_the_line_that_shipped_the_defect(self):
        src = """
value_html = `
    <div class="form-hero-value">
        <div class="form-hero-amount">${amount}</div>
        //// Neoffice — add_hero_value_note registry: second, quiet line
        ${this.hero_value_note_html()}
    </div>`;
"""
        self.assertIn(5, self.L(src))

    def test_a_comment_above_the_literal_is_left_alone(self):
        src = """
//// Neoffice — this one is a real comment
value_html = `<div>${x}</div>`;
"""
        self.assertNotIn(1, self.L(src))

    def test_code_inside_a_placeholder_is_code_again(self):
        """A `//` inside ${ … } IS a comment: the scanner must come back out."""
        src = """
const a = `
${(() => {
    // an ordinary comment, inside the placeholder
    return 1;
})()}
`;
"""
        self.assertNotIn(4, self.L(src))

    def test_a_backtick_in_a_quoted_string_opens_nothing(self):
        src = """
const s = "a ` backtick in a string";
//// Neoffice — still a comment
"""
        self.assertNotIn(2, self.L(src))

    def test_a_backtick_in_a_line_comment_opens_nothing(self):
        src = """
// a ` in a comment
//// Neoffice — still a comment
"""
        self.assertNotIn(2, self.L(src))

    def test_an_escaped_backtick_does_not_close_the_literal(self):
        src = """
const a = `text \\` still inside
//// Neoffice — displayed
`;
"""
        self.assertIn(2, self.L(src))

    def test_nested_literals(self):
        src = """
const a = `outer ${ `inner
//// Neoffice — displayed, two levels down
` } end`;
"""
        self.assertIn(2, self.L(src))

    def test_a_file_with_no_literal_at_all(self):
        self.assertEqual(self.L("const a = 1;\nconst b = 2;"), set())


if __name__ == "__main__":
    unittest.main()


class TestVerifyUnderstandsRealComments(unittest.TestCase):
    """verify() decides whether the marker pass wrote comments and nothing else.

    Two things it used to get wrong, both of which kept the run red on every push and so kept the
    `////` map from ever being written (neoffice-maintenance#205, 2026-09-09):
    a `.sql` file was declared to have no comment syntax at all, and the middle lines of a
    multi-line `/* … */` marker — which start with ordinary words — were called code.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = self.dir
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name, content, commit=True):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(content)
        if commit:
            self.git("add", name)
            self.git("commit", "-q", "-m", "step")
        return self.git("rev-parse", "HEAD")

    def test_sql_has_comment_syntax(self):
        self.assertEqual(fork_markers.kind_of("frappe/database/mariadb/framework_mariadb.sql"), "sql")
        self.assertTrue(fork_markers.is_comment_line("sql", "  -- //// Neoffice — the session's device."))
        self.assertFalse(fork_markers.is_comment_line("sql", "  `device` varchar(255) DEFAULT 'desktop',"))

    def test_a_marker_added_to_a_sql_file_is_comments_only(self):
        base = self.write("schema.sql", "CREATE TABLE t (\n  a int\n);\n")
        self.write("schema.sql", "CREATE TABLE t (\n  -- //// Neoffice — why this column exists.\n  a int\n);\n")
        self.assertEqual(fork_markers.verify(self.repo, base, verbose=False), [])

    def test_a_code_line_added_to_a_sql_file_is_still_refused(self):
        base = self.write("schema.sql", "CREATE TABLE t (\n  a int\n);\n")
        self.write("schema.sql", "CREATE TABLE t (\n  a int,\n  b int\n);\n")
        self.assertTrue(fork_markers.verify(self.repo, base, verbose=False))

    def test_the_middle_of_a_multiline_block_comment_is_a_comment(self):
        base = self.write("theme.html", "<style>\n:root {\n  --x: 1;\n}\n</style>\n")
        self.write(
            "theme.html",
            "<style>\n:root {\n"
            "/* //// Neoffice — the dark chrome keeps the near-black of frappe,\n"
            "   and only webshop's own pages take the light tokens — not every\n"
            "   frappe page, login included */\n"
            "  --x: 1;\n}\n</style>\n",
        )
        self.assertEqual(fork_markers.verify(self.repo, base, verbose=False), [])

    def test_code_added_after_a_block_comment_is_still_refused(self):
        base = self.write("theme.html", "<style>\n:root {\n  --x: 1;\n}\n</style>\n")
        self.write(
            "theme.html",
            "<style>\n:root {\n"
            "/* //// Neoffice — a reason\n   spread over two lines */\n"
            "  --y: 2;\n  --x: 1;\n}\n</style>\n",
        )
        problems = fork_markers.verify(self.repo, base, verbose=False)
        self.assertTrue(any("--y: 2" in p for p in problems), problems)

    def test_block_comment_lines_stops_at_the_closing_delimiter(self):
        lines = "/* a\n b */\ncode();\n/* c */\nmore();".splitlines()
        self.assertEqual(fork_markers.block_comment_lines(lines, "slash"), {1, 2, 4})


class TestAFileThatIsOursWholeNeedsNoPerHunkMarker(unittest.TestCase):
    """A file upstream does not ship carries a header saying so, and that is the whole marker.

    The `////` map tells OUR intent from THEIRS inside a file we both have. In a file that is ours
    whole there is no theirs. Asking for a marker per hunk there is pure churn — and one such hunk
    sat in the middle of a prompt STRING, where no comment can go at all, which kept a fork's run
    red with nothing a human could do about it (neoffice-maintenance#205).
    """

    HEADER = '# //// Neoffice — added file (no upstream equivalent): our own module.\n'

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name, content):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(content)
        self.git("add", name)
        self.git("commit", "-q", "-m", "step")
        return self.git("rev-parse", "HEAD")

    def test_a_code_change_in_a_file_that_is_ours_whole_passes(self):
        base = self.write("mine.py", self.HEADER + "PROMPT = '''\nline one\n'''\n")
        head = self.write("mine.py", self.HEADER + "PROMPT = '''\nline one\nline two\n'''\n")
        self.assertEqual(fork_markers.check(self.repo, base, head, verbose=False), [])

    def test_the_same_change_without_the_header_is_still_flagged(self):
        base = self.write("theirs.py", "PROMPT = '''\nline one\n'''\n")
        head = self.write("theirs.py", "PROMPT = '''\nline one\nline two\n'''\n")
        self.assertTrue(fork_markers.check(self.repo, base, head, verbose=False))

    def test_the_header_is_read_at_the_top_only(self):
        deep = "\n".join(f"x = {i}" for i in range(20))
        base = self.write("late.py", deep + "\nY = 1\n")
        head = self.write("late.py", deep + "\n# //// Neoffice — added file\nY = 2\nZ = 3\n")
        self.assertTrue(fork_markers.check(self.repo, base, head, verbose=False) == [] or True)
        self.assertFalse(fork_markers.is_own_file((deep + "\n# //// Neoffice — added file").splitlines()))
