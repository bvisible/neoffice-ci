"""A fork contract turns red when upstream brings back what we removed, or takes away what we added (#2398).

On 2026-10-20 an upstream merge gave the Item Group field of the Item form the filter "Is Group = No"; on 2026-10-30
the next merge moved it into JavaScript. Our items sit under parent groups, so the form stopped offering them. The fix
removes the filter and marks the removal, which protects nothing against the merge after that. Each case runs in a
throwaway git repository, because the script judges tracked files.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fork_contracts  # noqa: E402

ITEM_JS_FIXED = """frappe.ui.form.on("Item", {
\tsetup(frm) {
\t\t//// Neoffice — parent groups stay selectable (#2398)
\t\tfrm.set_query("uom", () => ({ filters: { enabled: 1 } }));
\t},
});
"""
ITEM_JS_UPSTREAM = ITEM_JS_FIXED.replace(
    '\t\t//// Neoffice — parent groups stay selectable (#2398)\n',
    '\t\tfrm.set_query("item_group", () => ({ filters: { is_group: 0 } }));\n',
)
ITEM_JSON_UPSTREAM = '{"fieldname": "item_group", "link_filters": "[[\\"Item Group\\",\\"is_group\\",\\"=\\",0]]"}\n'

MANIFEST = """# Fork markers

| Contract | Rule | Pattern | Why |
| --- | --- | --- | --- |
| `erpnext/stock/doctype/item/item.js` | absent | `set_query\\(\\s*"item_group"` | #2398: parent groups stay selectable |
| `erpnext/stock/doctype/item/item.json` | absent | `"link_filters":\\s*"\\[\\[\\\\"Item Group\\\\"` | #2398: same, in the JSON |
| `erpnext/stock/doctype/item/*.js` | present | `//// Neoffice — parent groups` | the marker that explains it |
"""


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class Fork:
    """A throwaway repository holding the Item form, with a contract manifest."""

    def __init__(self, test, js=ITEM_JS_FIXED, json_text=None, manifest=MANIFEST):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        git(self.root, "init", "-q")
        folder = self.root / "erpnext/stock/doctype/item"
        folder.mkdir(parents=True)
        (folder / "item.js").write_text(js)
        if json_text is not None:
            (folder / "item.json").write_text(json_text)
        if manifest is not None:
            (self.root / fork_contracts.MANIFEST).write_text(manifest)
        git(self.root, "add", "-A")

    def run(self):
        contracts, errors = fork_contracts.parse_contracts(
            (self.root / fork_contracts.MANIFEST).read_text().splitlines()
        )
        broken, notes = fork_contracts.judge(contracts, fork_contracts.tracked_files(self.root))
        return contracts, errors, broken, notes

    def cli(self):
        out = subprocess.run(
            [sys.executable, str(Path(fork_contracts.__file__)), "--root", str(self.root)],
            capture_output=True,
            text=True,
        )
        return out.returncode, out.stdout


class TestForkContracts(unittest.TestCase):
    def test_the_fixed_tree_keeps_its_contracts(self):
        contracts, errors, broken, _ = Fork(self).run()
        self.assertEqual((len(contracts), errors, broken), (3, [], []))

    def test_the_filter_coming_back_in_javascript_turns_it_red_with_its_line_and_its_reason(self):
        code, out = Fork(self, js=ITEM_JS_UPSTREAM).cli()
        self.assertEqual(code, 1)
        self.assertIn("BROKEN  erpnext/stock/doctype/item/item.js:3", out)
        self.assertIn("is back, we removed it (#2398: parent groups stay selectable)", out)

    def test_the_filter_coming_back_in_the_doctype_json_turns_it_red(self):
        fork = Fork(self, json_text=ITEM_JSON_UPSTREAM)
        _, _, broken, _ = fork.run()
        self.assertEqual(len(broken), 1)
        self.assertIn("erpnext/stock/doctype/item/item.json:1", broken[0])

    def test_upstream_dropping_our_marker_turns_a_present_contract_red(self):
        no_marker = ITEM_JS_FIXED.replace("//// Neoffice — parent groups stay selectable (#2398)", "// plain comment")
        _, _, broken, _ = Fork(self, js=no_marker).run()
        self.assertEqual(len(broken), 1)
        self.assertIn("is gone, we added it", broken[0])

    def test_a_present_contract_on_a_file_upstream_renamed_is_red_but_an_absent_one_is_only_a_note(self):
        manifest = (
            "| Contract | Rule | Pattern | Why |\n| --- | --- | --- | --- |\n"
            "| `erpnext/stock/doctype/item/old_name.js` | present | `//// Neoffice` | we added it |\n"
            "| `erpnext/stock/doctype/item/old_name.js` | absent | `set_query` | we removed it |\n"
        )
        _, _, broken, notes = Fork(self, manifest=manifest).run()
        self.assertEqual(len(broken), 1)
        self.assertIn("no such file", broken[0])
        self.assertEqual(len(notes), 1)
        self.assertIn("old_name.js", notes[0])

    def test_a_row_that_cannot_be_read_fails_the_run_instead_of_checking_nothing(self):
        bad = MANIFEST + "| `erpnext/x.js` | forbidden | `a` | why |\n| `erpnext/y.js` | absent | `(` | why |\n"
        _, errors, _, _ = Fork(self, manifest=bad).run()
        self.assertEqual(len(errors), 2)
        self.assertIn("`absent` or `present`", errors[0])
        self.assertIn("not a regular expression", errors[1])
        fork = Fork(self, manifest=bad)
        self.assertEqual(fork.cli()[0], 1)

    def test_an_alternation_is_written_with_an_escaped_bar(self):
        manifest = "| Contract | Rule | Pattern | Why |\n| - | - | - | - |\n| `a.js` | absent | `foo\\|bar` | why |\n"
        contracts, errors = fork_contracts.parse_contracts(manifest.splitlines())
        self.assertEqual(errors, [])
        self.assertEqual(contracts[0].pattern.pattern, "foo|bar")
        broken, _ = fork_contracts.judge(contracts, {"a.js": "x\nbar\n"})
        self.assertEqual(len(broken), 1)

    def test_no_manifest_and_no_table_judge_nothing_and_pass(self):
        self.assertEqual(Fork(self, manifest=None).cli()[0], 0)
        self.assertEqual(Fork(self, manifest="# Fork markers\n\n| Artifact | Why |\n| --- | --- |\n| `a/b.js` | built |\n").cli()[0], 0)

    def test_the_other_manifest_tables_are_not_read_as_contracts(self):
        manifest = MANIFEST + "\n| Binary | Why |\n| --- | --- |\n| `public/img/*.png` | images |\n"
        contracts, errors, _, _ = Fork(self, manifest=manifest).run()
        self.assertEqual((len(contracts), errors), (3, []))


if __name__ == "__main__":
    unittest.main()
