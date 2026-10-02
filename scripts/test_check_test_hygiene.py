"""scripts/check_test_hygiene.py: the two patterns that kill a Frappe test run for everyone, found by parsing."""

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_test_hygiene as hygiene  # noqa: E402


def found(source):
    return hygiene.check_source(textwrap.dedent(source))


def lines_of(source, kind=None):
    return [n for n, k, _ in found(source) if kind is None or k == kind]


class TestModuleLevelSkips(unittest.TestCase):
    def test_a_module_level_importorskip_is_found(self):
        self.assertEqual(lines_of('import pytest\npytest.importorskip("neoffice_theme.space_layout")\n', "error"), [2])

    def test_one_assigned_to_a_name_is_found(self):
        self.assertEqual(len(found('import pytest\nlayout = pytest.importorskip("x")\n')), 1)

    def test_a_skip_that_allows_the_module_level_is_found(self):
        self.assertEqual(lines_of('import pytest\nif True:\n    pytest.skip("no", allow_module_level=True)\n', "error"), [3])

    def test_one_inside_a_function_runs_only_when_called(self):
        self.assertEqual(found('import pytest\ndef t():\n    x = pytest.importorskip("x")\n'), [])

    def test_a_plain_skip_inside_a_test_is_fine(self):
        self.assertEqual(found('import pytest\ndef t():\n    pytest.skip("no site")\n'), [])

    def test_one_in_a_method_is_fine_but_one_in_a_class_body_is_not(self):
        method = 'import pytest\nclass T:\n    def test_a(self):\n        pytest.importorskip("x")\n'
        body = 'import pytest\nclass T:\n    x = pytest.importorskip("x")\n'
        self.assertEqual(found(method), [])
        self.assertEqual(len(found(body)), 1)

    def test_a_function_defined_under_an_if_is_still_a_function(self):
        src = 'import pytest\nif True:\n    def helper():\n        pytest.importorskip("x")\n'
        self.assertEqual(found(src), [])

    def test_the_main_guard_of_a_script_does_not_run_on_import(self):
        src = 'import pytest\nif __name__ == "__main__":\n    pytest.importorskip("x")\n'
        self.assertEqual(found(src), [])

    def test_the_else_of_the_main_guard_does(self):
        src = 'import pytest\nif __name__ == "__main__":\n    pass\nelse:\n    pytest.importorskip("x")\n'
        self.assertEqual(lines_of(src, "error"), [5])

    def test_the_mark_that_replaces_it_is_fine(self):
        src = 'import pytest\npytestmark = pytest.mark.skipif(True, reason="x")\n'
        self.assertEqual(found(src), [])


class TestLocalPatches(unittest.TestCase):
    HEAD = "from unittest.mock import patch\nimport frappe\n"

    def test_the_pattern_that_deleted_flags_is_a_warning(self):
        src = self.HEAD + 'patch.object(frappe.local, "flags", frappe._dict(), create=True)\n'
        self.assertEqual(lines_of(src, "warning"), [3])
        self.assertEqual(lines_of(src, "error"), [])

    def test_mock_dot_patch_is_found_too(self):
        src = 'from unittest import mock\nimport frappe\nwith mock.patch.object(frappe.local, "form_dict", {}, create=True):\n    pass\n'
        self.assertEqual(lines_of(src, "warning"), [3])

    def test_a_multi_line_call_is_found_at_its_first_line(self):
        src = 'import frappe\nfrom unittest.mock import patch\nx = patch.object(\n    frappe.local, "site", None,\n    create=True,\n)\n'
        self.assertEqual(lines_of(src, "warning"), [3])

    def test_every_attribute_a_bound_site_has_is_judged(self):
        for name in ("flags", "site", "conf", "db", "session", "form_dict", "lang", "message_log"):
            with self.subTest(name=name):
                src = self.HEAD + f'patch.object(frappe.local, "{name}", 1, create=True)\n'
                self.assertEqual(lines_of(src, "warning"), [3])

    def test_an_attribute_only_a_request_has_is_left_alone(self):
        for name in ("request", "request_ip", "login_manager", "website_profile"):
            with self.subTest(name=name):
                src = self.HEAD + f'patch.object(frappe.local, "{name}", None, create=True)\n'
                self.assertEqual(found(src), [])

    def test_a_name_that_is_not_a_literal_cannot_be_judged(self):
        src = self.HEAD + "patch.object(frappe.local, NAME, 1, create=True)\n"
        self.assertEqual(found(src), [])

    def test_without_create_the_attribute_is_put_back(self):
        src = self.HEAD + 'patch.object(frappe.local, "lang", "en")\n'
        self.assertEqual(found(src), [])

    def test_create_on_another_object_is_fine(self):
        src = (
            self.HEAD
            + 'patch.object(frappe.local.db, "commit", create=True)\n'
            + 'patch.object(frappe, "log_error", create=True)\n'
            + 'patch.dict(frappe.local.conf, {"a": 1})\n'
        )
        self.assertEqual(found(src), [])

    def test_create_false_is_fine(self):
        src = self.HEAD + 'patch.object(frappe.local, "lang", "en", create=False)\n'
        self.assertEqual(found(src), [])


class TestTheEscapeHatch(unittest.TestCase):
    def test_a_marked_line_is_left_alone(self):
        src = 'import pytest\npytest.importorskip("x")  # hygiene: ok this module is only ever run by pytest\n'
        self.assertEqual(found(src), [])

    def test_the_mark_covers_its_own_line_only(self):
        src = 'import pytest\npytest.importorskip("a")  # hygiene: ok\npytest.importorskip("b")\n'
        self.assertEqual(lines_of(src), [3])


class TestTheScan(unittest.TestCase):
    def tree(self, files):
        root = tempfile.mkdtemp()
        for path, text in files.items():
            full = os.path.join(root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as handle:
                handle.write(text)
        return root

    def test_only_test_modules_are_read(self):
        root = self.tree(
            {
                "app/tests/test_a.py": 'import pytest\npytest.importorskip("x")\n',
                "app/tests/helper.py": 'import pytest\npytest.importorskip("x")\n',
                "node_modules/pkg/test_b.py": 'import pytest\npytest.importorskip("x")\n',
            }
        )
        self.assertEqual([os.path.relpath(p, root) for p in hygiene.test_files(root)], ["app/tests/test_a.py"])

    def test_a_file_that_does_not_parse_is_not_this_scripts_business(self):
        self.assertEqual(hygiene.check_source("def broken(:\n"), [])


class TestTheExitCode(unittest.TestCase):
    def run_main(self, text, *args):
        import contextlib
        import io

        root = tempfile.mkdtemp()
        with open(os.path.join(root, "test_x.py"), "w") as handle:
            handle.write(text)
        out, err = io.StringIO(), io.StringIO()
        argv = sys.argv
        sys.argv = ["check_test_hygiene.py", "--root", root, *args]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = hygiene.main()
        finally:
            sys.argv = argv
        return code, out.getvalue()

    SKIP = 'import pytest\npytest.importorskip("x")\n'
    PATCH = 'from unittest.mock import patch\nimport frappe\npatch.object(frappe.local, "flags", 1, create=True)\n'

    def test_a_module_level_skip_fails_the_check(self):
        code, out = self.run_main(self.SKIP)
        self.assertEqual(code, 1)
        self.assertIn("test_x.py:2: error:", out)

    def test_a_local_patch_only_warns(self):
        code, out = self.run_main(self.PATCH)
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)
        self.assertIn("1 warning(s)", out)

    def test_strict_makes_the_warning_an_error(self):
        code, _ = self.run_main(self.PATCH, "--strict")
        self.assertEqual(code, 1)

    def test_a_clean_tree_passes(self):
        code, out = self.run_main("def test_a():\n    pass\n")
        self.assertEqual((code, out.strip()), (0, "test hygiene: ok"))


if __name__ == "__main__":
    unittest.main()
