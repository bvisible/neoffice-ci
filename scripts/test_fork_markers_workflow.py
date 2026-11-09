"""The fork-markers workflow measures what the marker pass wrote from the tree the pass started on (neoffice-maintenance#2320).

actions/checkout takes the branch TIP when the job starts. When another job (build-frontend) pushed its compiled files
before that, the tip is ahead of the pushed sha, and `fork_markers.py verify --base <pushed sha>` read those files as
non-comment lines the pass had written: a correct pass was discarded and the run went red. The workflow now records the
checked-out tip and verifies the pass against it.

Run: python3 -m unittest scripts/test_fork_markers_workflow.py
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import fork_markers  # noqa: E402

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "fork-markers.yml"


def commands(text, tool):
    """The lines of the workflow that run `fork_markers.py <tool>`, comments left out."""
    return [
        line.strip()
        for line in text.splitlines()
        if f"fork_markers.py {tool}" in line and not line.strip().startswith("#")
    ]


class TestTheWorkflowAnchorsThePassOnTheCheckedOutTip(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_range_step_records_the_tip_it_starts_from(self):
        step = self.text.split("- name: Resolve the range", 1)[1].split("- name: Check the markers", 1)[0]

        self.assertIn("TIP=$(git rev-parse HEAD)", step)
        self.assertIn('echo "tip=$TIP" >> "$GITHUB_OUTPUT"', step)

    def test_what_the_pass_wrote_is_measured_from_the_tip(self):
        for tool in ("verify", "format"):
            lines = commands(self.text, tool)
            self.assertTrue(lines, f"the workflow no longer runs fork_markers.py {tool}")
            for line in lines:
                with self.subTest(tool=tool):
                    self.assertIn('--base "${{ steps.range.outputs.tip }}"', line)

    def test_what_the_push_changed_is_still_measured_from_the_range(self):
        for line in commands(self.text, "check"):
            with self.subTest(line=line[:60]):
                self.assertNotIn("outputs.tip", line)
                self.assertIn("steps.range.outputs.base", line)


class TestAPassOnATreeAheadOfThePushedShaIsJudgedFromTheTip(unittest.TestCase):
    """The race itself, on a throwaway repository, with the real `verify`."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.commit({"app/code.py": "x = 1\n"}, "base")
        # the push: a change of ours with no marker
        self.pushed = self.commit({"app/code.py": "x = 2\n"}, "the push")
        # build-frontend pushed first in the meantime: compiled files no comment can be written into
        self.tip = self.commit({"app/public/assets/index-abc123.js": "console.log(1);\n"}, "frontend artifacts")
        # the marker pass, run on the checked-out tip: a comment and nothing else
        Path(self.repo, "app/code.py").write_text("# //// Neoffice — why x is 2\nx = 2\n", encoding="utf-8")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, files, message):
        for name, content in files.items():
            path = Path(self.repo, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def verify(self, base):
        with contextlib.redirect_stdout(io.StringIO()):
            return fork_markers.verify(self.repo, base, False)

    def test_judged_from_the_pushed_sha_the_files_that_arrived_meanwhile_count_against_the_pass(self):
        problems = self.verify(self.pushed)

        self.assertTrue(any("index-abc123.js" in p for p in problems), problems)

    def test_judged_from_the_tip_a_comment_only_pass_is_accepted(self):
        self.assertEqual(self.verify(self.tip), [])

    def test_judged_from_the_tip_a_pass_that_changes_code_is_still_refused(self):
        Path(self.repo, "app/code.py").write_text("# //// Neoffice — why\nx = 3\n", encoding="utf-8")

        problems = self.verify(self.tip)

        self.assertTrue(any("app/code.py" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
