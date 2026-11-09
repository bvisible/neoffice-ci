"""The upstream radar lists the lines a merge adds that narrow a screen (#2398).

The merge of 2026-10-20 added `"link_filters": "[[\\"Item Group\\",\\"is_group\\",\\"=\\",0]]"` to the Item form; the one of
2026-10-30 wrote the same rule as `frm.set_query("item_group", ...)`. Both must show up, with their line numbers, and
the ordinary lines of a merge must not.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import upstream_radar  # noqa: E402

DIFF = """diff --git a/erpnext/stock/doctype/item/item.json b/erpnext/stock/doctype/item/item.json
--- a/erpnext/stock/doctype/item/item.json
+++ b/erpnext/stock/doctype/item/item.json
@@ -176,0 +177,2 @@
+   "link_filters": "[[\\"Item Group\\",\\"is_group\\",\\"=\\",0]]",
+   "label": "Item Group",
@@ -300 +302 @@
-   "reqd": 0,
+   "reqd": 1,
diff --git a/erpnext/stock/doctype/item/item.js b/erpnext/stock/doctype/item/item.js
--- a/erpnext/stock/doctype/item/item.js
+++ b/erpnext/stock/doctype/item/item.js
@@ -40,0 +41 @@
+\t\tfrm.set_query("item_group", () => ({ filters: { is_group: 0 } }));
@@ -90,0 +92,2 @@
+\t\tfrm.toggle_display("x", true);
+\t\tfield.get_query = () => ({});
diff --git a/erpnext/stock/doctype/item/item.py b/erpnext/stock/doctype/item/item.py
--- a/erpnext/stock/doctype/item/item.py
+++ b/erpnext/stock/doctype/item/item.py
@@ -10,0 +11 @@
+\tfrappe.throw("set_query(")
diff --git a/erpnext/public/dist/js/bundle.js b/erpnext/public/dist/js/bundle.js
--- a/erpnext/public/dist/js/bundle.js
+++ b/erpnext/public/dist/js/bundle.js
@@ -1,0 +2 @@
+frm.set_query("built", 1)
"""


class TestUpstreamRadar(unittest.TestCase):
    def test_a_filter_declared_in_the_doctype_json_is_listed_with_its_line(self):
        found = upstream_radar.scan_diff(DIFF)
        json_filter = [f for f in found if f.kind == "link filter"]
        self.assertEqual([(f.path, f.line) for f in json_filter], [("erpnext/stock/doctype/item/item.json", 177)])

    def test_the_same_rule_written_in_javascript_is_listed(self):
        found = upstream_radar.scan_diff(DIFF)
        queries = [(f.path, f.line) for f in found if f.kind == "query filter"]
        self.assertEqual(queries, [("erpnext/stock/doctype/item/item.js", 41), ("erpnext/stock/doctype/item/item.js", 93)])

    def test_a_field_turned_mandatory_is_listed_and_the_line_it_replaces_is_not(self):
        mandatory = [f for f in upstream_radar.scan_diff(DIFF) if f.kind == "mandatory"]
        self.assertEqual([(f.path, f.line) for f in mandatory], [("erpnext/stock/doctype/item/item.json", 302)])

    def test_ordinary_lines_python_files_and_built_bundles_are_left_out(self):
        paths = {f.path for f in upstream_radar.scan_diff(DIFF)}
        self.assertEqual(paths, {"erpnext/stock/doctype/item/item.json", "erpnext/stock/doctype/item/item.js"})
        self.assertNotIn("toggle_display", upstream_radar.render(upstream_radar.scan_diff(DIFF), False))

    def test_an_empty_merge_says_so(self):
        self.assertEqual(upstream_radar.render([], False), "nothing that narrows a screen")

    def test_the_markdown_table_escapes_a_bar_inside_a_line(self):
        findings = [upstream_radar.Finding("query filter", "a.js", 3, 'set_query("x", a || b)')]
        self.assertIn("a // b", upstream_radar.render(findings, True))

    def test_end_to_end_on_a_real_merge_the_first_parent_is_the_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)

            git("init", "-q", "-b", "main")
            (root / "form.js").write_text('frappe.ui.form.on("Item", {});\n')
            git("add", "-A")
            git("commit", "-q", "-m", "ours")
            git("checkout", "-q", "-b", "upstream")
            (root / "form.js").write_text('frappe.ui.form.on("Item", {});\nfrm.set_query("item_group", {});\n')
            git("commit", "-q", "-am", "upstream restricts")
            git("checkout", "-q", "main")
            (root / "mine.txt").write_text("ours again\n")
            git("add", "-A")
            git("commit", "-q", "-m", "ours 2")
            git("merge", "-q", "--no-ff", "-m", "Merge upstream", "upstream")
            out = subprocess.run(
                [sys.executable, str(Path(upstream_radar.__file__)), "--base", "HEAD^1", "--head", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(out.returncode, 0)
            self.assertIn("query filter", out.stdout)
            self.assertIn("form.js:2", out.stdout)
            self.assertNotIn("mine.txt", out.stdout)


if __name__ == "__main__":
    unittest.main()
