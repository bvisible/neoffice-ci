"""A push that changes only documentation and translations skips the heavy suite; anything else, and any doubt, runs it.

Run: python3 -m unittest scripts/test_ci_light_change.py
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ci_light_change as lc  # noqa: E402

GREEN = "a" * 40
HEAD = "b" * 40
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "frappe-app-ci.yml"


def comparison(*files, status="ahead"):
    return {"status": status, "files": [dict(f) if isinstance(f, dict) else {"filename": f, "status": "modified"} for f in files]}


class TestWhatIsLight(unittest.TestCase):
    def test_documentation_and_catalogues_are_light(self):
        for p in (
            "README.md",
            "neoffice_theme/www/about.md",
            "docs/adr/0029-a-decision.md",
            "docs/diagram.svg",
            ".claude/skills/fleet-tracker/references/ci.md",
            ".claude/settings.json",
            "neoffice_theme/locale/fr.po",
            "neoffice_theme/locale/main.pot",
            "locale/de.po",
            "GLOSSARY.MD",
        ):
            self.assertTrue(lc.is_light(p), p)

    def test_code_and_everything_else_is_not(self):
        for p in (
            "neoffice_theme/hooks.py",
            "neoffice_theme/public/js/neoffice-theme.js",
            ".github/workflows/tests.yml",
            "neoffice_theme/translations/fr.csv",
            "neoffice_theme/locale/fr.mo",
            "neoffice_theme/locale/fr/LC_MESSAGES/x.po",
            "src/docs/page.vue",
            "pyproject.toml",
            "",
        ):
            self.assertFalse(lc.is_light(p), p)


class TestTheVerdictOnAComparison(unittest.TestCase):
    def test_documentation_only_is_light_and_names_the_catalogues_to_read(self):
        light, reason, po = lc.judge_comparison(comparison("docs/a.md", "neoffice_theme/locale/fr.po"))
        self.assertTrue(light, reason)
        self.assertEqual(po, ["neoffice_theme/locale/fr.po"])

    def test_one_code_file_runs_the_suite_and_says_which(self):
        light, reason, _ = lc.judge_comparison(comparison("docs/a.md", "neoffice_theme/api.py"))
        self.assertFalse(light)
        self.assertIn("neoffice_theme/api.py", reason)

    def test_a_forced_push_runs_the_suite(self):
        for status in ("diverged", "behind", "identical", None):
            light, reason, _ = lc.judge_comparison(comparison("docs/a.md", status=status))
            self.assertFalse(light, status)
            self.assertIn("not an ancestor", reason)

    def test_a_rename_out_of_the_code_is_not_light(self):
        moved = {"filename": "docs/old_module.md", "previous_filename": "app/old_module.py", "status": "renamed"}
        light, _, _ = lc.judge_comparison(comparison(moved))
        self.assertFalse(light)

    def test_a_removed_catalogue_is_light_but_not_read(self):
        gone = {"filename": "app/locale/it.po", "status": "removed"}
        light, _, po = lc.judge_comparison(comparison(gone, "app/locale/fr.po"))
        self.assertTrue(light)
        self.assertEqual(po, ["app/locale/fr.po"])

    def test_a_list_the_api_may_have_cut_runs_the_suite(self):
        light, reason, _ = lc.judge_comparison(comparison(*[f"docs/{i}.md" for i in range(lc.MAX_FILES)]))
        self.assertFalse(light)
        self.assertIn("ceiling", reason)

    def test_no_file_or_no_answer_runs_the_suite(self):
        self.assertFalse(lc.judge_comparison(comparison())[0])
        self.assertFalse(lc.judge_comparison(None)[0])
        self.assertFalse(lc.judge_comparison({"status": "ahead"})[0])


class TestDecideNeverGuesses(unittest.TestCase):
    def test_no_base_a_null_base_or_the_same_commit_runs_the_suite(self):
        for base in ("", "0" * 40, "not-a-sha", HEAD):
            with mock.patch.object(lc, "_get") as get:
                light, reason, _ = lc.decide("bvisible/app", base, HEAD, "token")
            self.assertFalse(light, base)
            get.assert_not_called()

    def test_no_token_runs_the_suite(self):
        self.assertFalse(lc.decide("bvisible/app", GREEN, HEAD, "")[0])

    def test_an_api_that_does_not_answer_runs_the_suite(self):
        with mock.patch.object(lc, "_get", return_value=None):
            self.assertFalse(lc.decide("bvisible/app", GREEN, HEAD, "token")[0])

    def test_it_compares_the_last_green_commit_with_the_head(self):
        with mock.patch.object(lc, "_get", return_value=comparison(".claude/skills/x/SKILL.md")) as get:
            light, _, _ = lc.decide("bvisible/app", GREEN, HEAD, "token")
        self.assertTrue(light)
        get.assert_called_once_with(f"/repos/bvisible/app/compare/{GREEN}...{HEAD}", "token")

    def test_a_classifier_that_breaks_leaves_the_suite_running(self):
        out = io.StringIO()
        with mock.patch.object(lc, "decide", side_effect=RuntimeError("boom")), redirect_stdout(out), \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            code = lc.main(["decide", "--repo", "bvisible/app", "--base", GREEN, "--head", HEAD])
        self.assertEqual(code, 0)
        self.assertIn("light=false", out.getvalue())

    def test_the_verdict_reaches_github_output(self):
        with tempfile.NamedTemporaryFile("r", suffix=".out") as fh, \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": fh.name, "GITHUB_TOKEN": "t"}), \
                mock.patch.object(lc, "_get", return_value=comparison("app/locale/fr.po")), redirect_stdout(io.StringIO()):
            lc.main(["decide", "--repo", "bvisible/app", "--base", GREEN, "--head", HEAD])
            lines = Path(fh.name).read_text().splitlines()
        self.assertIn("light=true", lines)
        self.assertEqual(json.loads(next(x for x in lines if x.startswith("po="))[3:]), ["app/locale/fr.po"])


class TestTheLastGreenRun(unittest.TestCase):
    def test_it_reads_the_head_of_the_last_successful_push_run(self):
        with mock.patch.object(lc, "_get", return_value={"workflow_runs": [{"head_sha": GREEN}]}) as get:
            self.assertEqual(lc.last_success("bvisible/neoffice-devops", "ci.yml", "main", "t"), GREEN)
        path = get.call_args[0][0]
        self.assertIn("/actions/workflows/ci.yml/runs?", path)
        for part in ("branch=main", "event=push", "status=success"):
            self.assertIn(part, path)

    def test_none_or_no_answer_is_empty(self):
        for answer in (None, {}, {"workflow_runs": []}, {"workflow_runs": [{"head_sha": "short"}]}):
            with mock.patch.object(lc, "_get", return_value=answer):
                self.assertEqual(lc.last_success("bvisible/neoffice-devops", "ci.yml", "main", "t"), "")


class TestTheGateOfTheAppCi(unittest.TestCase):
    """The reusable workflow keeps the doubt on the side of the suite."""

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_tests_skip_only_on_an_explicit_false(self):
        self.assertIn("needs.gate.outputs.run != 'false'", self.text)

    def gate(self):
        return self.text.split("\n  gate:", 1)[1].split("\n  light-check:", 1)[0]

    def test_a_push_compares_from_the_last_green_run_not_from_before(self):
        gate = self.gate()
        self.assertIn("ci_light_change.py decide", gate)
        self.assertIn('--base "$last_sha"', gate)
        self.assertNotIn("github.event.before", gate)

    def test_a_rerun_always_runs(self):
        self.assertIn("github.run_attempt", self.gate())

    def test_the_light_check_reads_the_catalogues_with_frappes_reader(self):
        light = self.text.split("\n  light-check:", 1)[1].split("\n  tests:", 1)[0]
        self.assertIn("needs.gate.outputs.light == 'true'", light)
        self.assertIn("check_po_reads.py", light)
        self.assertIn("Babel~=", light)


if __name__ == "__main__":
    unittest.main()
