"""The build + tests job runs the same steps on GitHub's runners and on our CI machine.

frappe-app-ci.yml carries that job twice since 2026-11-11: `tests` on ubuntu-latest, and `tests-mini` on our CI
machine (a Linux VM on the Mac mini) for a PRIVATE repository whose variable CI_RUNNER is `neoffice-mini` (GitHub
bills a private repository's minutes). A step added to one job and forgotten in the other would make a repository's
verdict depend on where it ran, silently. This reads both jobs from the file, without a YAML library (the self tests
install none): the same step names in the same order, except the few that only make sense on one side; the same
conditions for the job to run, but for the variable that picks the side; and no `services:` on our machine, which
has no Docker (MariaDB and Redis are its own).
"""

import pathlib
import re
import unittest

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "frappe-app-ci.yml"
# Every other job follows the variable too: "the CI must cost us nothing" (Jérémy, 11.11) — GitHub bills each job of
# a private repository, rounded up to the minute. Only `client-names` stays on GitHub: it runs for PUBLIC repositories
# alone, and those are free there.
CHOSEN = "${{ vars.CI_RUNNER == 'neoffice-mini' && fromJSON('[\"self-hosted\",\"linux\",\"arm64\",\"neoffice-mini\"]') || 'ubuntu-latest' }}"
STAYS_ON_GITHUB = {("frappe-app-ci.yml", "client-names"), ("frappe-app-ci.yml", "tests")}

# Steps that exist on one side only, each for a reason written next to it in the workflow.
ONLY_ON_GITHUB = {
    "Cache pip",  # the machine keeps its pip, uv and yarn caches on its own disk
    "Install MariaDB client and wkhtmltopdf (patched Qt build, as frappe's own CI)",  # installed once on the machine
}
ONLY_ON_THE_MACHINE = {
    "Take a place on the CI machine (three heavy suites at most at once)",
    "MariaDB client, Redis and wkhtmltopdf of the machine (installed once, nothing installed here)",
    "Free the place (database, bench and Redis of this job removed)",
}
ON_GITHUB = "vars.CI_RUNNER != 'neoffice-mini'"
ON_THE_MACHINE = "vars.CI_RUNNER == 'neoffice-mini'"


def job_block(text, job):
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.S | re.M)
    if not match:
        raise AssertionError(f"job {job!r} not found in {WORKFLOW.name}")
    return match.group(1)


def step_names(block):
    return re.findall(r"^      - name: (.+?)\s*$", block, re.M)


def job_condition(block):
    match = re.search(r"^    if: \$\{\{ (.+?) \}\}\s*$", block, re.M)
    if not match:
        raise AssertionError("the job has no `if: ${{ … }}` line")
    return match.group(1)


def without(condition, clause):
    parts = [part.strip() for part in condition.split("&&")]
    if clause not in parts:
        raise AssertionError(f"`{clause}` missing from the condition `{condition}`")
    return [part for part in parts if part != clause]


def jobs_and_runners(path):
    text = path.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)[1]
    for match in re.finditer(r"(?ms)^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", body):
        runs_on = re.search(r"(?m)^    runs-on: (.+?)\s*$", match.group(2))
        yield match.group(1), runs_on.group(1) if runs_on else None


class EveryJobOfAPrivateRepositoryCanRunOnOurMachine(unittest.TestCase):
    def test_every_job_follows_the_variable(self):
        for name in ("frappe-app-ci.yml", "translate-po.yml"):
            for job, runs_on in jobs_and_runners(WORKFLOWS / name):
                if (name, job) in STAYS_ON_GITHUB or job == "tests-mini":
                    continue
                with self.subTest(workflow=name, job=job):
                    self.assertEqual(runs_on, CHOSEN)


class TheTwoTestJobsRunTheSameSteps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = WORKFLOW.read_text(encoding="utf-8")
        cls.github = job_block(text, "tests")
        cls.machine = job_block(text, "tests-mini")

    def test_the_same_steps_in_the_same_order(self):
        github = [name for name in step_names(self.github) if name not in ONLY_ON_GITHUB]
        machine = [name for name in step_names(self.machine) if name not in ONLY_ON_THE_MACHINE]
        self.assertEqual(github, machine)

    def test_the_steps_said_to_be_on_one_side_exist_there(self):
        self.assertLessEqual(ONLY_ON_GITHUB, set(step_names(self.github)))
        self.assertLessEqual(ONLY_ON_THE_MACHINE, set(step_names(self.machine)))

    def test_the_same_conditions_but_for_the_side(self):
        self.assertEqual(
            without(job_condition(self.github), ON_GITHUB),
            without(job_condition(self.machine), ON_THE_MACHINE),
        )

    def test_our_machine_runs_no_service_container(self):
        self.assertNotRegex(self.machine, r"(?m)^    services:")
        self.assertRegex(self.machine, r"(?m)^    runs-on: \[self-hosted, linux, arm64, neoffice-mini\]$")

    def test_the_place_is_freed_whatever_happened(self):
        last = re.split(r"(?m)^      - name: ", self.machine)[-1]
        self.assertTrue(last.startswith("Free the place"), "the release must be the last step")
        self.assertRegex(last, r"(?m)^        if: always\(\)$")


if __name__ == "__main__":
    unittest.main()
