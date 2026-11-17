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
        self.write(name, f"# {message}\n")
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
        self.write(WATCHED, "# staged\n")
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
        self.git("merge", "-q", "--no-ff", "side", "-m", "Merge branch side")
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
