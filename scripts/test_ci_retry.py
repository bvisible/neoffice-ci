"""scripts/ci_retry.sh: a command that fails for a moment is retried, a command that always fails still fails."""

import os
import subprocess
import tempfile
import unittest

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_retry.sh")


def run(body, attempts="3"):
    """Run `body` in a bash that has sourced the helper, with no waiting between attempts."""
    env = dict(os.environ, CI_RETRY_DELAY="0", CI_RETRY_ATTEMPTS=attempts)
    return subprocess.run(
        ["bash", "-e", "-c", f". '{HELPER}'\n{body}"], capture_output=True, text=True, env=env, timeout=60
    )


class TestRetry(unittest.TestCase):
    def setUp(self):
        # a command that fails its first `fail_times` calls, then succeeds; the calls are counted in a file
        handle, self.counter = tempfile.mkstemp(prefix="retry-count-")
        os.close(handle)
        self.addCleanup(os.unlink, self.counter)
        self.flaky = f"""
        flaky() {{
          n=$(( $(cat '{self.counter}' 2>/dev/null || echo 0) + 1 )); echo $n > '{self.counter}'
          [ "$n" -gt "$1" ]
        }}
        """

    def calls(self):
        with open(self.counter) as handle:
            return int(handle.read() or 0)

    def test_a_command_that_passes_runs_once(self):
        out = run(self.flaky + "retry flaky 0")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.calls(), 1)
        self.assertNotIn("::warning::", out.stdout)

    def test_a_command_that_fails_twice_then_passes_gets_through(self):
        out = run(self.flaky + "retry flaky 2")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.calls(), 3)
        self.assertEqual(out.stdout.count("::warning::attempt"), 2)

    def test_a_command_that_always_fails_fails_after_the_last_attempt_with_its_own_status(self):
        out = run("retry bash -c 'exit 7'")
        self.assertEqual(out.returncode, 7, out.stdout + out.stderr)
        self.assertIn("::error::'bash' failed 3 times", out.stdout)

    def test_the_number_of_attempts_is_the_one_asked_for(self):
        out = run(self.flaky + "retry flaky 99", attempts="5")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(self.calls(), 5)

    def test_the_arguments_of_the_command_are_never_printed(self):
        # a `bench get-app` carries its access token in its arguments
        out = run("retry bash -c 'exit 1' --token SECRET-VALUE-123")
        self.assertNotIn("SECRET-VALUE-123", out.stdout + out.stderr)

    def test_a_failure_inside_the_function_called_by_retry_is_seen_through_its_last_command(self):
        # `set -e` is off inside a function called from `until`: the command that decides must come last
        out = run("step() { false; true; }\nretry step")
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
