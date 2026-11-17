#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_key_line as key_line  # noqa: E402

SCRIPT = Path(__file__).with_name("check_key_line.py")
WATCHED = "app/api/reading.py"


class Repository(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="key_line_"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)], check=False))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("app/settings.py", 'SPLIT = frappe.conf.get("scan_split_by_rate")\n')
        self.configure(key_line_since="2026-11-18")
        self.base = self.commit("the repository as it was", "2026-11-18T08:00:00")

    def git(self, *args, **environment):
        done = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            env=dict(os.environ, **environment),
            check=True,
        )
        return done.stdout.strip()

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def configure(self, **more):
        config = {"watched": [{"path": "app/api/reading*.py", "why": "the reading of a scan"}], **more}
        self.write(key_line.CONFIG, json.dumps(config))

    def commit(self, message, date="2026-11-19T10:00:00"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        return self.git("rev-parse", "HEAD")

    def change(self, name, message, date="2026-11-19T10:00:00"):
        self.write(name, f"prompt = {message.splitlines()[0]!r}\n")
        return self.commit(message, date)

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args], capture_output=True, text=True, timeout=60
        )

    def gate(self, message, *files):
        self.write("message.txt", message)
        return self.run_script("--message", str(self.root / "message.txt"), "--files", *files)

    def push(self):
        return self.run_script("--base", self.base)


class TheGateBeforeACommit(Repository):
    def test_a_watched_file_with_no_line_is_refused_and_says_what_to_write(self):
        done = self.gate("feat(reading): a new prompt\n", WATCHED)
        self.assertEqual(done.returncode, 1)
        self.assertIn("no `Key:` line", done.stdout)
        self.assertIn("Key: none, <why nothing changes for a customer>, reviewed by <who>", done.stdout)

    def test_a_key_that_exists_in_the_repository_is_accepted(self):
        done = self.gate("feat(reading): a line per rate\n\nKey: scan_split_by_rate\n", WATCHED, "README.md")
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_a_key_written_nowhere_guards_nothing(self):
        done = self.gate("feat(reading): x\n\nKey: scan_whole_lines\n", WATCHED)
        self.assertEqual(done.returncode, 1)
        self.assertIn("is written nowhere in the repository", done.stdout)

    def test_none_says_why_and_who_reviewed(self):
        accepted = self.gate("test(reading): x\n\nKey: none, tests and translations only, reviewed by the pilot\n", WATCHED)
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        for line in ("Key: none", "Key: none, tests, reviewed by the pilot", "Key: none, tests and translations only"):
            with self.subTest(line=line):
                self.assertEqual(self.gate(f"test(reading): x\n\n{line}\n", WATCHED).returncode, 1)

    def test_a_commit_that_touches_no_watched_path_needs_no_line(self):
        self.assertEqual(self.gate("docs: a typo\n", "README.md", "app/api/chat.py").returncode, 0)

    def test_as_a_commit_msg_hook_it_reads_the_staged_files(self):
        self.write("message.txt", "feat(reading): a new prompt\n")
        self.write(WATCHED, "staged = 1\n")
        self.git("add", WATCHED)
        refused = self.run_script("--message", str(self.root / "message.txt"))
        self.assertEqual(refused.returncode, 1)
        self.git("reset", "-q", "--", WATCHED)
        self.write("README.md", "# staged\n")
        self.git("add", "README.md")
        self.assertEqual(self.run_script("--message", str(self.root / "message.txt")).returncode, 0)

    def test_the_word_key_inside_a_sentence_is_not_the_line(self):
        done = self.gate("feat(reading): x\n\nGuarded, see the note. Key: scan_split_by_rate\n", WATCHED)
        self.assertEqual(done.returncode, 1)


class ThePushedCommits(Repository):
    def test_a_commit_pushed_without_its_line_is_refused_with_its_sha_and_subject(self):
        sha = self.change(WATCHED, "feat(reading): a new prompt")
        done = self.push()
        self.assertEqual(done.returncode, 1)
        self.assertIn(sha[:10], done.stdout)
        self.assertIn("feat(reading): a new prompt", done.stdout)
        self.assertIn("Key-For:", done.stdout)

    def test_commits_with_their_line_and_commits_elsewhere_pass(self):
        self.change(WATCHED, "feat(reading): a line per rate\n\nKey: scan_split_by_rate")
        self.change("app/api/chat.py", "feat(chat): a new answer")
        done = self.push()
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_a_later_commit_repairs_an_earlier_one_and_a_wrong_repair_does_not(self):
        sha = self.change(WATCHED, "feat(reading): a new prompt")
        self.change("notes.md", f"chore: the key of an earlier commit\n\nKey-For: {sha[:9]} scan_whole_lines")
        self.assertEqual(self.push().returncode, 1, "a key written nowhere repairs nothing")
        self.change("notes.md", f"chore: the key of an earlier commit, again\n\nKey-For: {sha[:9]} scan_split_by_rate")
        self.assertEqual(self.push().returncode, 0)

    def test_a_commit_made_before_the_rule_started_is_not_judged(self):
        self.change(WATCHED, "feat(reading): from before the rule", "2026-11-17T09:00:00")
        self.assertEqual(self.push().returncode, 0)

    def test_a_merge_is_not_judged_and_its_own_commits_are(self):
        self.git("checkout", "-q", "-b", "side")
        self.change(WATCHED, "feat(reading): on a side branch\n\nKey: scan_split_by_rate")
        self.git("checkout", "-q", "main")
        self.change("README.md", "docs: meanwhile")
        day = "2026-11-19T12:00:00"  # made after the rule started, or it would be left out for its date alone
        self.git("merge", "-q", "--no-ff", "side", "-m", "Merge branch side", GIT_COMMITTER_DATE=day, GIT_AUTHOR_DATE=day)
        self.assertEqual(self.push().returncode, 0)

    def test_without_a_base_the_last_days_are_read(self):
        self.change(WATCHED, "feat(reading): a new prompt", date=self.git("log", "-1", "--format=%cI").replace("2026", "2099"))
        self.assertEqual(self.run_script("--days", "14").returncode, 1)


class TheWeekOfWarnings(Repository):
    """A pushed message cannot be corrected: before the day the repository names, a missing line is only said."""

    def setUp(self):
        super().setUp()
        self.configure(key_line_since="2026-11-18", key_line_blocks_from="2026-11-24")
        self.base = self.commit("the rule gets its week of warnings", "2026-11-18T09:00:00")

    def test_a_commit_made_before_that_day_is_said_as_a_warning_and_does_not_fail_the_run(self):
        sha = self.change(WATCHED, "feat(reading): a new prompt", "2026-11-23T23:00:00")
        done = self.push()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertRegex(done.stdout, rf"^::warning::{sha[:10]} « feat\(reading\): a new prompt » touches")
        self.assertIn("A commit made from 2026-11-24 on without its key line is refused.", done.stdout)
        self.assertIn("1 commit(s) made before 2026-11-24 without their key line: said, not refused.", done.stdout)

    def test_a_commit_made_from_that_day_on_is_refused(self):
        self.change(WATCHED, "feat(reading): warned", "2026-11-23T10:00:00")
        sha = self.change(WATCHED, "feat(reading): refused", "2026-11-24T00:30:00")
        done = self.push()
        self.assertEqual(done.returncode, 1)
        self.assertRegex(done.stdout, rf"(?m)^{sha[:10]} « feat\(reading\): refused »")
        self.assertEqual(done.stdout.count("::warning::"), 1)

    def test_the_gate_before_a_commit_refuses_from_the_first_day(self):
        done = self.gate("feat(reading): a new prompt\n", WATCHED)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("::warning::", done.stdout)

    def test_a_commit_with_its_line_is_neither_warned_nor_refused(self):
        self.change(WATCHED, "feat(reading): a line per rate\n\nKey: scan_split_by_rate", "2026-11-20T10:00:00")
        done = self.push()
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_a_day_that_is_not_a_day_fails_the_run(self):
        self.configure(key_line_since="2026-11-18", key_line_blocks_from="next week")
        done = self.push()
        self.assertEqual(done.returncode, 1)
        self.assertIn("`key_line_blocks_from` is a day", done.stdout)


class WhereTheLineIsAsked(Repository):
    """Asked of every commit, the line would be written without a thought: it is asked where code changes."""

    KEY = "\n\nKey: scan_split_by_rate"

    def setUp(self):
        super().setUp()
        watched = [
            {"path": "app/api/reading*.py", "why": "the reading of a scan"},
            {"path": "app/api/reading_words.py", "why": "the words shown under an account", "key_line": False},
            {"path": "app/mobile.py", "why": "the booking of a scan, among everything else", "key_line": False},
            {"path": "app/scan/*", "why": "the scanned document"},
            {"path": "app/public/window.js", "why": "the booking window"},
        ]
        self.write(key_line.CONFIG, json.dumps({"watched": watched, "key_line_since": "2026-11-18"}))
        self.write(WATCHED, "def read(scan):\n    return scan.total\n")
        self.write("app/public/window.js", "function book(scan) {\n\treturn scan.total;\n}\n")
        self.base = self.commit("the code as it is" + self.KEY)

    def test_a_path_watched_for_its_lists_only_asks_for_no_line(self):
        self.change("app/mobile.py", "feat(till): a new button")
        self.change("app/api/reading_words.py", "feat(words): new words, in a file a wider pattern also matches")
        self.assertEqual((self.push().returncode, self.push().stdout), (0, ""))
        self.assertEqual(self.gate("feat(till): a new button\n", "app/mobile.py").returncode, 0)

    def test_a_test_in_a_watched_path_asks_for_no_line(self):
        self.change("app/scan/test_scan.py", "test(scan): a new case")
        self.change("app/scan/tests/cases.py", "test(scan): more cases")
        self.change("app/scan/scan.test.js", "test(scan): the window")
        self.assertEqual(self.push().returncode, 0, self.push().stdout)
        self.assertEqual(self.gate("feat(scan): code\n", "app/scan/scan.py").returncode, 1, "its code still does")

    def test_a_change_of_comments_only_asks_for_no_line(self):
        self.write(WATCHED, "# why the total is read here\n\ndef read(scan):\n    # the total, as printed\n    return scan.total\n")
        self.write("app/public/window.js", "// the window books what the scan read\nfunction book(scan) {\n\treturn scan.total;\n}\n")
        self.assertEqual(self.gate("docs(reading): why\n", WATCHED, "app/public/window.js").returncode, 0)
        self.commit("docs(reading): why the total is read here")
        self.assertEqual((self.push().returncode, self.push().stdout), (0, ""))

    def test_code_turned_into_a_comment_is_a_change_of_code(self):
        self.write(WATCHED, "def read(scan):\n    scan.check()\n    return scan.total\n")
        self.base = self.commit("feat(reading): the scan is checked before it is read" + self.KEY)
        self.write(WATCHED, "def read(scan):\n    # scan.check()\n    return scan.total\n")
        refused = self.gate("fix(reading): no check\n", WATCHED)
        self.assertEqual(refused.returncode, 1, "the only line added is a comment, the line removed was code")
        self.commit("fix(reading): no check")
        self.assertEqual(self.push().returncode, 1)

    def test_a_comment_beside_a_change_of_code_does_not_excuse_it(self):
        self.write(WATCHED, "def read(scan):\n    # the gross total\n    return scan.gross\n")
        self.assertEqual(self.gate("fix(reading): gross\n", WATCHED).returncode, 1)
        self.write("app/public/window.js", "// a comment only\nfunction book(scan) {\n\treturn scan.total;\n}\n")
        self.assertEqual(self.gate("fix(reading): gross\n", WATCHED, "app/public/window.js").returncode, 1)

    def test_a_new_file_is_code_until_it_is_read(self):
        self.write("app/scan/new_module.py", "# only a comment for now\n")
        self.assertEqual(self.gate("feat(scan): a module\n", "app/scan/new_module.py").returncode, 1)


MOBILE = """
import re


def till_button(order):
    return order.total


def _rank(line):
    return line.amount


def _split(lines):
    # one line per rate
    return sorted(lines, key=_rank)


def _flag(value):
    return bool(value)


def create_invoice(scan):
    \"\"\"Creates the purchase invoice of a scan.\"\"\"
    return _split(scan.lines)


def preview_entry(scan):
    return _flag(scan.paid)


class Bank:
    def pay(self, invoice):
        return invoice.total
"""


class TheFunctionsOfAFile(Repository):
    """In a file that mixes what is booked with everything else, the line is asked for the functions the registry
    names, and for the functions of the file they run."""

    KEY = "\n\nKey: scan_split_by_rate"
    FILE = "app/mobile.py"

    def setUp(self):
        super().setUp()
        self.named = [
            {"function": "create_invoice", "why": "creates the purchase invoice of a scan"},
            {"function": "preview_entry", "why": "says the entry a paid scan will make"},
            {"function": "Bank.pay", "why": "writes the payment of an invoice"},
        ]
        self.register()
        self.write(self.FILE, MOBILE)
        self.base = self.commit("the file as it is" + self.KEY)

    def register(self, **more):
        watched = [{"path": self.FILE, "why": "the booking of a scan, among everything else", "key_line": self.named}]
        self.write(key_line.CONFIG, json.dumps({"watched": watched, "key_line_since": "2026-11-18", **more}))

    def edit(self, old, new, message=None):
        text = (self.root / self.FILE).read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        self.write(self.FILE, text.replace(old, new))
        return self.commit(message) if message else None

    def asked(self, old, new):
        """Whether the change asks for the line: the same answer before the commit and once it is pushed."""
        self.edit(old, new)
        gate = self.gate("fix: a change\n", self.FILE)
        self.commit("fix: a change")
        pushed = self.push()
        self.assertEqual(gate.returncode, pushed.returncode, gate.stdout + pushed.stdout)
        return pushed

    def test_a_function_nobody_named_and_no_named_function_runs_asks_for_no_line(self):
        done = self.asked("return order.total", "return order.total + order.tip")
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_a_named_function_asks_for_the_line_and_is_named_in_the_refusal(self):
        done = self.asked("return _split(scan.lines)", "return _split(scan.lines)[:1]")
        self.assertEqual(done.returncode, 1)
        self.assertIn("touches app/mobile.py (create_invoice)", done.stdout)

    def test_a_function_a_named_function_runs_asks_for_the_line_a_callback_too(self):
        done = self.asked("return sorted(lines, key=_rank)", "return sorted(lines, key=_rank, reverse=True)")
        self.assertIn("touches app/mobile.py (_split)", done.stdout)
        done = self.asked("return line.amount", "return -line.amount")
        self.assertEqual(done.returncode, 1)
        self.assertIn("(_rank)", done.stdout)

    def test_a_method_named_with_its_class_asks_for_the_line(self):
        done = self.asked("return invoice.total", "return invoice.total - invoice.discount")
        self.assertIn("(Bank.pay)", done.stdout)

    def test_a_new_helper_is_read_as_soon_as_a_named_function_runs_it(self):
        self.edit("def _flag(value):", "def _net(scan):\n    return scan.total\n\n\ndef _flag(value):")
        self.edit("return _flag(scan.paid)", "return _flag(scan.paid) and _net(scan)", "feat: the net of an entry" + self.KEY)
        self.base = self.git("rev-parse", "HEAD")
        done = self.asked("    return scan.total\n\n\ndef _flag", "    return scan.total - scan.vat\n\n\ndef _flag")
        self.assertEqual(done.returncode, 1)
        self.assertIn("(_net)", done.stdout)

    def test_comments_a_docstring_and_a_function_that_only_moved_are_not_a_change(self):
        self.edit("    # one line per rate\n", "    # one line per rate of the document, sorted\n")
        self.edit('    """Creates the purchase invoice of a scan."""\n', '    """Creates the invoice."""\n')
        text = (self.root / self.FILE).read_text(encoding="utf-8")
        moved = "def _flag(value):\n    return bool(value)\n\n\n"
        self.write(self.FILE, text.replace(moved, "") + "\n\n" + moved.rstrip("\n") + "\n")
        self.assertEqual(self.gate("docs: words\n", self.FILE).returncode, 0)
        self.commit("docs: words")
        self.assertEqual((self.push().returncode, self.push().stdout), (0, ""))

    def test_a_new_function_nobody_named_passes_which_is_the_limit_of_the_list(self):
        self.edit("class Bank:", "def create_payment(scan):\n    return scan.total\n\n\nclass Bank:")
        self.assertEqual(self.gate("feat: a payment\n", self.FILE).returncode, 0)

    def test_a_file_that_no_longer_parses_is_taken_as_changed(self):
        self.edit("def till_button(order):", "def till_button(order:")
        self.assertEqual(self.gate("fix: x\n", self.FILE).returncode, 1)

    def test_a_function_the_registry_names_and_the_file_no_longer_holds_is_refused(self):
        self.edit("def preview_entry(scan):", "def preview_of_the_entry(scan):")
        gate = self.gate("refactor: a clearer name" + self.KEY + "\n", self.FILE)
        self.assertEqual(gate.returncode, 1)
        self.assertIn("app/mobile.py no longer holds the function preview_entry", gate.stdout)
        self.commit("refactor: a clearer name" + self.KEY)
        done = self.push()
        self.assertEqual(done.returncode, 1)
        self.assertIn("name the function that took its place", done.stdout)
        self.named[1]["function"] = "preview_of_the_entry"
        self.register()
        self.commit("chore: the registry follows" + self.KEY)
        self.assertEqual(self.push().returncode, 0, self.push().stdout)

    def test_the_file_of_another_author_is_not_read_from_the_working_tree(self):
        self.edit("def preview_entry(scan):", "def preview_of_the_entry(scan):")
        self.assertEqual(self.gate("docs: a typo\n", "README.md").returncode, 0, "not a file of this commit")
        self.assertEqual(self.gate("chore: registry\n", key_line.CONFIG).returncode, 1, "the registry itself is")

    def test_a_function_is_named_with_its_reason_in_one_file(self):
        self.named.append({"function": "create_payment"})
        self.register()
        done = self.gate("docs: x\n", "README.md")
        self.assertEqual(done.returncode, 1)
        self.assertIn("the function create_payment is named without its reason", done.stdout)
        self.named.pop()
        self.write(
            key_line.CONFIG,
            json.dumps({"watched": [{"path": "app/*.py", "why": "x", "key_line": self.named}], "key_line_since": "2026-11-18"}),
        )
        self.assertIn("names one file, not a pattern", self.gate("docs: x\n", "README.md").stdout)

    def test_a_named_function_keeps_its_week_of_warnings(self):
        self.register(key_line_blocks_from="2026-11-24")
        self.base = self.commit("chore: a week of warnings" + self.KEY)
        self.edit("return _split(scan.lines)", "return _split(scan.lines)[:1]")
        self.commit("fix: one line", "2026-11-20T10:00:00")
        done = self.push()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertRegex(done.stdout, r"^::warning::\w{10} « fix: one line » touches app/mobile.py \(create_invoice\)")


class NotStarted(Repository):
    def test_without_the_configuration_or_its_starting_day_nothing_is_judged(self):
        self.change(WATCHED, "feat(reading): a new prompt")
        self.configure()
        done = self.push()
        self.assertEqual(done.returncode, 0)
        self.assertIn("has not started here", done.stdout)
        (self.root / key_line.CONFIG).unlink()
        done = self.push()
        self.assertEqual(done.returncode, 0)
        self.assertIn("no commit is judged", done.stdout)


if __name__ == "__main__":
    unittest.main()
