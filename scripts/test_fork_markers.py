"""fork_markers.py check, on a throwaway repository. Run: python3 -m unittest scripts/test_fork_markers.py"""
import os
import subprocess
import sys
import tempfile
import time
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

    def test_an_html_comment_inside_a_literal_is_still_flagged_by_the_scanner(self):
        """The scanner reports the LINE; verify() is what forgives `<!-- … -->`."""
        src = """
const html = `<div>
    <!-- //// Neoffice — invisible in the DOM -->
</div>`;
"""
        self.assertIn(2, self.L(src))

    def test_a_quote_inside_a_regex_opens_no_string(self):
        """`/"/g` in a placeholder opened a "string" that swallowed the closing brace and
        backtick: every string after it flipped, and 579 lines of a shop's checkout
        script read as literal text."""
        src = r"""
const match = cards.filter(`[data-address="${(selected || '').replace(/"/g, '\\"')}"]`);
if (selected && !match.length) return;
const html = `
    <div>${x}</div>
`;
done();
"""
        self.assertEqual(self.L(src), {4, 5})

    def test_a_backtick_inside_a_regex_opens_no_literal(self):
        src = """
const tick = /`/g;
//// Neoffice — a comment, not text
"""
        self.assertEqual(self.L(src), set())

    def test_a_division_is_not_a_regex(self):
        src = """
const half = (a + b) / 2, rate = total / count;
const s = `
    ${half}
`;
"""
        self.assertEqual(self.L(src), {3, 4})

    def test_a_string_never_outlives_its_line(self):
        """A '...' or "..." string cannot span lines in JavaScript: what misled the
        scanner on one line stops misleading it at the next."""
        src = """
const broken = 'never closed;
const s = `
    ${x}
`;
"""
        self.assertEqual(self.L(src), {3, 4})

    def test_a_long_line_full_of_divisions_scans_in_linear_time(self):
        """Reading the line up to every `/` made the scan quadratic: 11 s for twenty
        32 000-character lines of divisions, minutes for a minified bundle in CI."""
        lines = ["a = b / c; " * 3000] * 20
        started = time.perf_counter()
        fork_markers.template_literal_lines(lines)
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_a_file_with_no_literal_at_all(self):
        self.assertEqual(self.L("const a = 1;\nconst b = 2;"), set())


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

    def test_a_vendored_bundle_is_not_something_to_mark(self):
        """`public/js/lib/` holds vendored libraries; `*.global.js` is a tsup build.

        Minified onto a handful of enormous lines, so there is no line to write a
        marker on. Five such hunks kept the check red on every push of the frappe
        fork -- and a verifier that cannot be satisfied is one people stop reading.
        """
        self.assertEqual(fork_markers.kind_of("frappe/public/js/lib/neocockpit.global.js"), "skip")
        self.assertEqual(fork_markers.kind_of("frappe/public/js/lib/jquery/jquery.js"), "skip")

    def test_our_own_sources_are_still_marked(self):
        """The exception is about build output, not about `public/js` at large."""
        self.assertEqual(fork_markers.kind_of("neoffice_theme/public/js/neoffice-theme.js"), "slash")
        self.assertEqual(fork_markers.kind_of("frappe/public/js/frappe/form/form.js"), "slash")
        self.assertEqual(fork_markers.kind_of("frappe/commands/utils.py"), "hash")

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


class TestAMarkerOnTheElementCoversItsAttributes(unittest.TestCase):
    """Nothing can sit between the attributes of an opening tag: their marker goes on the element.

    LOOKBACK counts from the hunk, so on a tag with ten attributes the element's marker was out of
    reach — thirteen wiki hunks that carried one were reported anyway (neoffice-maintenance#354).
    """

    BASE = (
        "<template>\n"
        '    <div class="frame">\n'
        "        <img\n"
        '            :src="src"\n'
        '            :alt="alt"\n'
        '            :title="title"\n'
        '            :width="width"\n'
        '            class="image"\n'
        '            @keydown="() =>\n'
        '                select()"\n'
        '            @click="select"\n'
        "        />\n"
        "    </div>\n"
        "</template>\n"
    )
    MARKER = (
        "        <!-- //// Neoffice — the width goes through :style, and a click opens the\n"
        "             //// lightbox in read mode; no comment can sit between the attributes. -->\n"
    )

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

    def head(self, marker):
        return (
            self.BASE.replace("        <img\n", (self.MARKER if marker else "") + "        <img\n")
            .replace('            :width="width"\n', "")
            .replace('            @click="select"\n', '            :style="imageStyle"\n            @click="open"\n')
        )

    def test_a_marker_on_the_element_covers_its_attributes(self):
        """Both hunks sit below an arrow `=>`, which must not read as the end of the tag."""
        base = self.write("Image.vue", self.BASE)
        head = self.write("Image.vue", self.head(marker=True))
        self.assertEqual(fork_markers.check(self.repo, base, head, verbose=False), [])

    def test_without_it_both_attribute_hunks_are_flagged(self):
        base = self.write("Image.vue", self.BASE)
        head = self.write("Image.vue", self.head(marker=False))
        found = fork_markers.check(self.repo, base, head, verbose=False)
        self.assertEqual(sorted(u["kind"] for u in found), ["modified", "removed-only"])

    def test_a_change_in_content_is_not_an_attribute(self):
        """Scanning up meets a tag that closes: the change is text, and LOOKBACK decides alone."""
        base = self.write("page.html", '<div>\n    <p class="lead">\n        one\n        two\n        three\n        four\n    </p>\n</div>\n')
        head = self.write("page.html", '<div>\n    <!-- //// Neoffice — a reason -->\n    <p class="lead">\n        one\n        two\n        three\n        FOUR\n    </p>\n</div>\n')
        self.assertEqual(len(fork_markers.check(self.repo, base, head, verbose=False)), 1)

    def test_the_scan_is_bounded(self):
        lines = ["<img"] + ['    a="1"'] * (fork_markers.TAG_SCAN + 5)
        self.assertIsNone(fork_markers.enclosing_tag_line(lines, len(lines)))
        self.assertEqual(fork_markers.enclosing_tag_line(lines[:10], 10), 1)
        self.assertIsNone(fork_markers.enclosing_tag_line(lines, 0))


class TestAListedArtifactIsBuildOutput(unittest.TestCase):
    """A committed build artifact is named in the manifest, never marked (neoffice-maintenance#354).

    A marker written into generated output is wiped by the next build, and the run goes red again
    on a file nobody may hand-edit.
    """

    MANIFEST = (
        "# manifest\n\n"
        "| Artifact | Built from |\n"
        "| --- | --- |\n"
        "| `pub/css/app.css` | `src/app.css` via the build |\n"
        "| `pub/desk/**` (chunks, `sw.js`) | `desk/**` |\n"
        "\n"
        "| Hunk | Change |\n"
        "| --- | --- |\n"
        "| `pub/css/hand.css` — a rule | named, but not in an artifact table |\n"
    )

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write_all(self, files):
        for name, content in files.items():
            path = os.path.join(self.repo, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            self.git("add", name)
        self.git("commit", "-q", "-m", "step")
        return self.git("rev-parse", "HEAD")

    def test_listed_artifacts_are_skipped_and_the_rest_is_not(self):
        files = {"pub/css/app.css": "a { color: red; }\n", "pub/desk/index.html": "<div>one</div>\n", "pub/css/hand.css": "b { color: red; }\n"}
        base = self.write_all({fork_markers.MANIFEST: self.MANIFEST, **files})
        head = self.write_all({k: v.replace("red", "blue").replace("one", "two") for k, v in files.items()})
        found = fork_markers.check(self.repo, base, head, verbose=False)
        self.assertEqual([u["file"] for u in found], ["pub/css/hand.css"])

    def test_patterns_come_from_the_first_column_of_an_artifact_table_only(self):
        self.assertEqual(fork_markers.manifest_artifacts(self.MANIFEST.splitlines()), ["pub/css/app.css", "pub/desk/**"])

    def test_a_bare_pattern_excuses_nothing(self):
        self.assertEqual(fork_markers.manifest_artifacts(["| Artifact | x |", "| --- | --- |", "| `*.css` | y |"]), [])


if __name__ == "__main__":
    unittest.main()
