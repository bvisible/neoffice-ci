"""What mirror_check.py lets through and what it stops (python3 -m unittest discover -s scripts -p "test_*.py").

An upstream preview measures the cost of the fork upgrade only if its call is the normal call's, inputs AND secrets: on
2026-11-09 the theme's preview had the tests' apps but was given CI_REPORT_SECRET alone, so the private wizard's clone
was refused before any test ("get_dep failed 3 times") while the check said the two callers agreed.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror_check  # noqa: E402

CALL = """name: {name}
on:
  workflow_dispatch:
jobs:
  ci:
    uses: bvisible/neoffice-ci/.github/workflows/frappe-app-ci.yml@main
    with:
      app: neoffice_theme
      install_apps: "{apps}"
{extra}{secrets}
"""
INHERIT = "    secrets: inherit\n"
REPORT_ONLY = "    secrets:\n      CI_REPORT_SECRET: ${{ secrets.CI_REPORT_SECRET }}\n"


def call(name, apps="erpnext,payments", secrets=INHERIT, extra=""):
    return CALL.format(name=name, apps=apps, secrets=secrets, extra=extra)


class TestTheMirrorCheck(unittest.TestCase):
    def run_check(self, tests_yml, preview_yml):
        with tempfile.TemporaryDirectory() as root:
            workflows = Path(root, ".github", "workflows")
            workflows.mkdir(parents=True)
            (workflows / "tests.yml").write_text(tests_yml, encoding="utf8")
            (workflows / "upstream-preview.yml").write_text(preview_yml, encoding="utf8")
            here = os.getcwd()
            out = io.StringIO()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(out):
                    code = mirror_check.main()
            finally:
                os.chdir(here)
        return code, out.getvalue()

    def test_the_same_inputs_and_the_same_secrets_pass(self):
        code, said = self.run_check(
            call("Tests"), call("Upstream preview", extra="      upstream_preview: true\n")
        )
        self.assertEqual(code, 0, said)

    def test_an_input_the_preview_lacks_is_stopped(self):
        code, said = self.run_check(
            call("Tests", apps="erpnext,payments,neoffice_wizard"),
            call("Upstream preview", extra="      upstream_preview: true\n"),
        )
        self.assertEqual(code, 1)
        self.assertIn("install_apps", said)

    def test_secrets_the_preview_is_not_given_are_stopped(self):
        # the theme's preview of 09.11: the tests inherit the secrets, the preview names CI_REPORT_SECRET alone
        code, said = self.run_check(
            call("Tests"),
            call("Upstream preview", secrets=REPORT_ONLY, extra="      upstream_preview: true\n"),
        )
        self.assertEqual(code, 1, said)
        self.assertIn("secrets", said)
        self.assertIn("inherit", said)
        self.assertIn("CI_REPORT_SECRET", said)

    def test_the_same_secrets_named_alike_pass(self):
        code, said = self.run_check(
            call("Tests", secrets=REPORT_ONLY),
            call("Upstream preview", secrets=REPORT_ONLY, extra="      upstream_preview: true\n"),
        )
        self.assertEqual(code, 0, said)

    def test_a_preview_given_more_secrets_than_the_tests_passes(self):
        # erpnextswiss and nora: the tests name CI_REPORT_SECRET, the preview inherits everything
        code, said = self.run_check(
            call("Tests", secrets=REPORT_ONLY),
            call("Upstream preview", extra="      upstream_preview: true\n"),
        )
        self.assertEqual(code, 0, said)

    def test_no_preview_is_nothing_to_mirror(self):
        with tempfile.TemporaryDirectory() as root:
            here = os.getcwd()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mirror_check.main(), 0)
            finally:
                os.chdir(here)


if __name__ == "__main__":
    unittest.main()
