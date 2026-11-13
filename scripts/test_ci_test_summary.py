"""The summary a red run sends to the tracker names the failing tests, and copies nothing else of the log.

Run: python3 -m unittest scripts/test_ci_test_summary.py (no dependency).
The logs below have the shapes the runners print (bench run-tests, bench run-parallel-tests, pytest -q, node --test
in TAP and in spec); every name in them is made up.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(__file__))
import ci_test_summary  # noqa: E402

SEP1 = "=" * 70
SEP2 = "-" * 70

# The shape of 2026-11-13: unittest's verdict on stderr, then the prints of the tests on stdout, so the end of the
# log says nothing of what failed.
UNITTEST_LOG = f"""\
.....s.....................EOF marker not found
.....................ssss.......
{SEP1}
ERROR: test_opens_the_page (myapp.tests.test_pages.TestThePage.test_opens_the_page)
{SEP2}
Traceback (most recent call last):
  File "/home/runner/frappe-bench/apps/myapp/myapp/tests/test_pages.py", line 12, in test_opens_the_page
    page = open_page("secret-value-1")
frappe.exceptions.DoesNotExistError: DocType Example Page not found

{SEP1}
FAIL: test_the_total (myapp.tests.test_orders.TestOrders.test_the_total)
{SEP2}
Traceback (most recent call last):
  File "/home/runner/frappe-bench/apps/myapp/myapp/tests/test_orders.py", line 40, in test_the_total
    self.assertEqual(total, 12)
AssertionError: 13 != 12 : the order of someone@example.com

{SEP1}
FAIL: test_rounding (myapp.tests.test_orders.TestOrders.test_rounding) (percent=33.3)
{SEP2}
AssertionError: 0.35 != 0.3

{SEP1}
FAIL: test_rounding (myapp.tests.test_orders.TestOrders.test_rounding) (percent=66.6)
{SEP2}
AssertionError: 0.65 != 0.7

{SEP2}
Ran 812 tests in 157.059s

FAILED (failures=3, errors=1, skipped=68)
<frappe.test_runner.TimeLoggingTestResult run=812 errors=1 failures=3>

Setting up test environment...
   Created Company: Example Test Company
Log Settings: Deleted Document kept 30 -> 180 days
Roles withdrawn: {{'users': ['someone@example.com']}}
Domain activated: Website
"""

UNITTEST_GREEN = f"""\
..........
{SEP2}
Ran 10 tests in 1.000s

OK (skipped=2)
"""

TAP_LOG = """\
TAP version 13
# Subtest: a suite of cards
    # Subtest: a card shows its title
    not ok 1 - a card shows its title
      ---
      duration_ms: 1.2
      type: 'test'
      location: '/home/runner/work/myapp/myapp/myapp/tests/js/cards.test.js:20:3'
      failureType: 'testCodeFailure'
      error: 'the value secret-value-2 is wrong'
      code: 'ERR_TEST_FAILURE'
      name: 'TypeError'
      stack: |-
        render (/home/runner/work/myapp/myapp/myapp/public/js/cards.js:10:5)
      ...
    1..1
not ok 1 - a suite of cards
  ---
  duration_ms: 3.4
  type: 'suite'
  location: '/home/runner/work/myapp/myapp/myapp/tests/js/cards.test.js:18:1'
  failureType: 'subtestsFailed'
  error: '1 subtest failed'
  code: 'ERR_TEST_FAILURE'
  ...
# Subtest: the total is rounded \\# twice
not ok 2 - the total is rounded \\# twice
  ---
  duration_ms: 0.8
  type: 'test'
  location: '/home/runner/work/myapp/myapp/myapp/tests/js/totals.test.js:7:1'
  failureType: 'testCodeFailure'
  error: |-
    Expected values to be strictly equal:

    0.35 !== 0.3
  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  expected: 0.3
  actual: 0.35
  operator: 'strictEqual'
  ...
# Subtest: not written yet
not ok 3 - not written yet # TODO
  ---
  duration_ms: 0.1
  ...
ok 4 - a passing test
  ---
  duration_ms: 0.1
  ...
1..4
# tests 5
# suites 1
# pass 1
# fail 2
# cancelled 0
# skipped 0
# todo 1
# duration_ms 120.5
"""

SPEC_LOG = """\
▶ a suite of cards
  ✖ a card shows its title (1.2ms)
    TypeError [Error]: the value secret-value-3 is wrong
        at render (/home/runner/work/myapp/myapp/myapp/public/js/cards.js:10:5)
✖ a suite of cards (3.4ms)
✔ a passing test (0.1ms)
ℹ tests 3
ℹ suites 1
ℹ pass 1
ℹ fail 2
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 120.5

✖ failing tests:

test at myapp/tests/js/cards.test.js:20:3
✖ a card shows its title (1.2ms)
  TypeError [Error]: the value secret-value-3 is wrong
      at render (/home/runner/work/myapp/myapp/myapp/public/js/cards.js:10:5)

test at myapp/tests/js/totals.test.js:7:1
✖ the total is rounded (0.8ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:

  0.35 !== 0.3
"""

PYTEST_LOG = """\
==== pytest myapp/tests/unit
..F.E.
_________________________ TestCart.test_add _________________________
myapp/tests/unit/test_cart.py:14: in test_add
    assert cart.total == 3
E   assert 4 == 3
=========================== short test summary info ============================
FAILED ../apps/myapp/myapp/tests/unit/test_cart.py::TestCart::test_add - assert 4 == 3
FAILED ../apps/myapp/myapp/tests/unit/test_cart.py::test_price[secret-value-4-12] - AssertionError: 12 != 13
ERROR ../apps/myapp/myapp/tests/unit/test_stock.py::test_level - KeyError: 'secret-value-5'
2 failed, 3 passed, 1 error in 4.56s
"""

PARALLEL_LOG = f"""\
myapp.tests.test_orders.TestOrders
   ✖  test_the_total

{SEP1}
 FAIL  test_the_total (myapp.tests.test_orders.TestOrders.test_the_total)
{SEP2}
AssertionError: 13 != 12
<frappe.parallel_test_runner.ParallelTestResult run=40 errors=0 failures=1>
"""

OLD_PYTHON_LOG = f"""\
{SEP1}
FAIL: test_the_total (myapp.tests.test_orders.TestOrders)
{SEP2}
AssertionError: 13 != 12

{SEP1}
ERROR: test_broken_module (unittest.loader._FailedTest.test_broken_module)
{SEP2}
ImportError: Failed to import test module: test_broken_module
ModuleNotFoundError: No module named 'secret_value_6'

{SEP2}
Ran 41 tests in 2.000s

FAILED (failures=1, errors=1)
"""

SECRETS = ("secret-value", "secret_value", "someone@example.com", "Example Test Company", "13 != 12", "0.35")


class Logs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def log(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def summary(self, *texts, limit=ci_test_summary.DEFAULT_LIMIT):
        paths = [self.log(f"log{i}.log", t) for i, t in enumerate(texts)]
        return ci_test_summary.summarize(paths, limit)

    def assertNothingCopied(self, out):
        for value in SECRETS:
            self.assertNotIn(value, out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("/home/runner", out)


class TestUnittest(Logs):
    def test_the_failing_tests_are_named_even_when_prints_follow_the_verdict(self):
        out = self.summary(UNITTEST_LOG)
        lines = out.splitlines()
        self.assertEqual(
            lines[0],
            "Tests serveur (unittest) : Ran 812 tests in 157.059s · FAILED (failures=3, errors=1, skipped=68)",
        )
        self.assertEqual(
            lines[1:],
            [
                "- ERROR test_pages.TestThePage.test_opens_the_page → DoesNotExistError",
                "- FAIL test_orders.TestOrders.test_the_total",
                "- FAIL test_orders.TestOrders.test_rounding (2 sous-tests)",
            ],
        )
        self.assertNotIn("Log Settings", out)
        self.assertNothingCopied(out)

    def test_the_older_python_form_and_a_module_that_does_not_import(self):
        out = self.summary(OLD_PYTHON_LOG)
        self.assertIn("- FAIL test_orders.TestOrders.test_the_total\n", out + "\n")
        self.assertIn("- ERROR test_broken_module → ModuleNotFoundError", out)
        self.assertNothingCopied(out)

    def test_the_parallel_runner(self):
        out = self.summary(PARALLEL_LOG)
        self.assertEqual(
            out.splitlines(),
            ["Tests serveur (unittest) : run=40 errors=0 failures=1", "- FAIL test_orders.TestOrders.test_the_total"],
        )

    def test_a_green_suite_next_to_a_red_one_says_it_is_green(self):
        out = self.summary(UNITTEST_GREEN, TAP_LOG)
        self.assertTrue(out.startswith("Tests serveur (unittest) : Ran 10 tests in 1.000s · OK (skipped=2)\n"), out)
        self.assertIn("- FAIL cards.test.js:20 — a card shows its title → TypeError", out)


class TestNode(Logs):
    def test_tap_names_the_failing_tests_not_their_suite_nor_a_todo(self):
        out = self.summary(TAP_LOG)
        self.assertEqual(
            out.splitlines(),
            [
                "Tests JS (node --test) : tests 5 · pass 1 · fail 2",
                "- FAIL cards.test.js:20 — a card shows its title → TypeError",
                "- FAIL totals.test.js:7 — the total is rounded # twice",
            ],
        )
        self.assertNothingCopied(out)

    def test_spec_reads_the_closing_list(self):
        out = self.summary(SPEC_LOG)
        self.assertEqual(
            out.splitlines(),
            [
                "Tests JS (node --test) : tests 3 · pass 1 · fail 2",
                "- FAIL cards.test.js:20 — a card shows its title → TypeError",
                "- FAIL totals.test.js:7 — the total is rounded",
            ],
        )
        self.assertNothingCopied(out)

    def test_cancelled_tests_are_said(self):
        out = self.summary(SPEC_LOG.replace("ℹ cancelled 0", "ℹ cancelled 4"))
        self.assertIn("fail 2 · cancelled 4", out)


class TestPytest(Logs):
    def test_ids_without_their_parameters_nor_the_message(self):
        out = self.summary(PYTEST_LOG)
        self.assertEqual(
            out.splitlines(),
            [
                "pytest : 2 failed, 3 passed, 1 error",
                "- FAIL test_cart.py::TestCart::test_add",
                "- FAIL test_cart.py::test_price (1 sous-test)",
                "- ERROR test_stock.py::test_level → KeyError",
            ],
        )
        self.assertNothingCopied(out)


class TestBoundsAndLastResort(Logs):
    def test_many_failures_keep_the_verdict_and_count_the_ones_left_out(self):
        blocks = "".join(
            f"{SEP1}\nFAIL: test_case_{i:03d}_with_a_rather_long_name_that_says_what_it_proves "
            f"(myapp.tests.test_many.TestMany.test_case_{i:03d}_with_a_rather_long_name_that_says_what_it_proves)\n"
            f"{SEP2}\nAssertionError: 1 != 2\n\n"
            for i in range(60)
        )
        log = blocks + f"{SEP2}\nRan 60 tests in 1.0s\n\nFAILED (failures=60)\n"
        out = self.summary(log, limit=1400)
        self.assertLessEqual(len(out), 1400)
        lines = out.splitlines()
        self.assertEqual(lines[0], "Tests serveur (unittest) : Ran 60 tests in 1.0s · FAILED (failures=60)")
        shown = [x for x in lines if x.startswith("- FAIL ")]
        self.assertGreater(len(shown), 5)
        self.assertEqual(lines[-1], f"… et {60 - len(shown)} autres tests en échec : lire le run.")

    def test_a_long_name_is_cut(self):
        name = "test_" + "x" * 300
        log = f"{SEP1}\nFAIL: {name} (myapp.tests.test_long.TestLong.{name})\n{SEP2}\nAssertionError\n"
        out = self.summary(log)
        line = out.splitlines()[1]
        self.assertTrue(line.endswith("…"), line)
        self.assertLessEqual(len(line), ci_test_summary.NAME_LIMIT + len("- FAIL "))

    def test_two_long_names_that_start_alike_stay_two_tests(self):
        stem = "test_" + "y" * 200
        log = "".join(
            f"{SEP1}\nFAIL: {stem}_{end} (myapp.tests.test_long.TestLong.{stem}_{end})\n{SEP2}\nAssertionError\n"
            for end in ("first", "second")
        )
        lines = self.summary(log).splitlines()
        self.assertEqual(len(lines), 3, lines)  # the verdict line is missing in this log: header + two tests
        self.assertEqual(lines[1], lines[2])  # both cut the same way when shown, and still counted as two

    def test_a_suite_killed_mid_run_gives_the_end_of_its_log(self):
        log = "\n".join(f"line {i} of a suite that never finished" for i in range(500)) + "\n"
        out = self.summary(log, limit=600)
        self.assertLessEqual(len(out), 600)
        self.assertTrue(out.startswith("Aucun test en échec reconnu dans le journal ; sa fin :\n"), out)
        self.assertTrue(out.endswith("line 499 of a suite that never finished"), out)
        self.assertTrue(out.splitlines()[1].startswith("line "), out)  # cut at a line boundary

    def test_the_end_comes_from_the_log_that_is_not_green(self):
        killed = "starting the JavaScript tests\nthe runner was killed\n"
        out = self.summary(UNITTEST_GREEN, killed)
        self.assertIn("OK (skipped=2)", out)
        self.assertTrue(out.endswith("the runner was killed"), out)

    def test_no_log_gives_nothing_and_a_missing_one_is_skipped(self):
        missing = os.path.join(self.dir.name, "absent.log")
        self.assertEqual(ci_test_summary.summarize([missing]), "")
        out = ci_test_summary.summarize([missing, self.log("a.log", TAP_LOG)])
        self.assertIn("fail 2", out)

    def test_colour_codes_and_carriage_returns_are_read_through(self):
        coloured = UNITTEST_LOG.replace("FAIL: test_the_total", "\x1b[31mFAIL: test_the_total").replace("\n", "\r\n")
        out = self.summary(coloured)
        self.assertIn("- FAIL test_orders.TestOrders.test_the_total", out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\r", out)


class TestCommandLine(Logs):
    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ci_test_summary.main(list(argv))
        return code, buf.getvalue()

    def test_prints_the_summary_and_exits_0(self):
        code, out = self.run_main("--limit", "1400", self.log("a.log", UNITTEST_LOG))
        self.assertEqual(code, 0)
        self.assertIn("- FAIL test_orders.TestOrders.test_the_total", out)

    def test_prints_nothing_without_a_log(self):
        code, out = self.run_main(os.path.join(self.dir.name, "absent.log"))
        self.assertEqual((code, out), (0, ""))


if __name__ == "__main__":
    unittest.main()
