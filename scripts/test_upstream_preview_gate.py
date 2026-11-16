"""An upstream preview plays its tests at every pass; the fork CI may still skip a pass where nothing moved.

The gate of frappe-app-ci.yml skips "build + tests" when neither the app nor bvisible/frappe moved since the last green
run. An upstream preview runs on UPSTREAM frappe/frappe and frappe/erpnext instead, which move on their own every day:
judged the same way, the previews of Monday 16.11 were skipped and reported green with no test played (nora run
43809052995, erpnextswiss run 43808393271). This runs the gate's own shell, read from the workflow, with `curl`
answering as the hub and GitHub would on such a Monday.

Run: python3 -m unittest scripts/test_upstream_preview_gate.py
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "frappe-app-ci.yml"
SHA = "1" * 40


def gate_script():
    """The `run: |` block of the gate's decide step, as the runner gets it."""
    text = WORKFLOW.read_text(encoding="utf-8")
    gate = re.search(r"(?ms)^  gate:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n)", text).group(1)
    step = gate[gate.index("id: decide") :]
    block = re.search(r"(?ms)^        run: \|\n(.*?)(?=^      - |\Z)", step).group(1)
    return textwrap.dedent(block)


def gnu_date():
    if not shutil.which("date"):
        return False
    done = subprocess.run(["date", "-u", "-d", "2026-11-16T05:00:00Z", "+%s"], capture_output=True, text=True)
    return done.returncode == 0


@unittest.skipUnless(shutil.which("bash") and gnu_date(), "needs bash and GNU date (the runner's)")
class TestTheGate(unittest.TestCase):
    def decide(self, upstream):
        """Run the gate on a schedule where nothing of the app nor of bvisible/frappe moved since a green run of
        yesterday; return what it wrote to GITHUB_OUTPUT."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            green_at = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            frappe_at = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            curl = tmp / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                f'  *last_green*) echo \'{{"message": {{"sha": "{SHA}", "at": "{green_at}"}}}}\' ;;\n'
                f'  *repos/bvisible/frappe/commits*) echo \'{{"commit": {{"committer": {{"date": "{frappe_at}"}}}}}}\' ;;\n'
                "  *) exit 22 ;;\n"
                "esac\n"
            )
            curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
            output, summary = tmp / "output", tmp / "summary"
            env = {
                "PATH": f"{tmp}:{os.environ['PATH']}",
                "HUB": "https://hub.invalid/api/method/neoffice_devops.github.ci.last_green",
                "CI_SECRET": "x",
                "GH_TOKEN": "x",
                "ATTEMPT": "1",
                "FLEET_TOKEN": "x",
                "REPO": "bvisible/an-app",
                "BRANCH": "version-15",
                "SHA": SHA,
                "EVENT": "schedule",
                "UPSTREAM": "true" if upstream else "false",
                "MAX_AGE_DAYS": "7",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            done = subprocess.run(["bash", "-c", gate_script()], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            return dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)

    def test_the_fork_ci_skips_a_pass_where_nothing_moved(self):
        """The harness is right: on such a Monday the ordinary CI spends nothing."""
        decided = self.decide(upstream=False)
        self.assertEqual(decided["run"], "false", decided["reason"])

    def test_an_upstream_preview_plays_its_tests_all_the_same(self):
        decided = self.decide(upstream=True)
        self.assertEqual(decided["run"], "true", decided["reason"])
        self.assertIn("upstream", decided["reason"])


class TestTheInputReachesTheGate(unittest.TestCase):
    def test_the_gate_reads_the_input(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        gate = re.search(r"(?ms)^  gate:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n)", text).group(1)
        self.assertTrue("UPSTREAM: ${{ inputs.upstream_preview }}" in gate, "the gate does not read upstream_preview")


if __name__ == "__main__":
    unittest.main()
