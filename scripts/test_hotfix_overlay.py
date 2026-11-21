"""Tests of scripts/hotfix_overlay.py: what the hotfix-overlay workflow decides (neoffice-maintenance#3237).

The git steps are played on a scratch repository, with `refs/remotes/origin/version-15` standing for the deploy branch.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import hotfix_overlay as h

IDENTITY = {
    "GIT_AUTHOR_NAME": "A",
    "GIT_AUTHOR_EMAIL": "a@example.test",
    "GIT_COMMITTER_NAME": "A",
    "GIT_COMMITTER_EMAIL": "a@example.test",
}
SHA = "0123456789abcdef0123456789abcdef01234567"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env={**os.environ, **IDENTITY}
    ).stdout.strip()


def commit(repo, files: dict, message: str) -> str:
    for name, content in files.items():
        path = Path(repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class TestTheInputsAreExactlyWhatTheyShouldBe(unittest.TestCase):
    def ok(self, **over):
        values = dict(app="pos_next", base=SHA, commits=[SHA[::-1]], hotfix_id="HF-0001", branch="version-15")
        values.update(over)
        return h.validate_inputs(values["app"], values["base"], values["commits"], values["hotfix_id"], values["branch"])

    def refused(self, **over):
        with self.assertRaises(h.Refusal) as caught:
            self.ok(**over)
        return caught.exception.code

    def test_good_inputs_pass(self):
        self.assertIsNone(self.ok())

    def test_a_short_or_upper_case_hash_is_refused(self):
        for bad in (SHA[:7], SHA.upper(), "g" * 40, SHA + "0", ""):
            self.assertEqual(self.refused(base=bad), "bad_input", bad)
            self.assertEqual(self.refused(commits=[bad]), "bad_input", bad)

    def test_the_identifier_is_opaque(self):
        for bad in ("HF-1", "hf-0001", "SRV-9999", "HF-0001\n", "HF-0001 ", "HF-0001;id", "", "HF-0001-acme"):
            self.assertEqual(self.refused(hotfix_id=bad), "bad_input", repr(bad))

    def test_the_app_and_the_branch_are_plain_names(self):
        for bad in ("POS_NEXT", "pos-next", "pos_next;id", "../x", "p", ""):
            self.assertEqual(self.refused(app=bad), "bad_input", bad)
        for bad in ("main", "version-1", "version-15/../x", "version-15\n", ""):
            self.assertEqual(self.refused(branch=bad), "bad_input", repr(bad))

    def test_no_commit_a_commit_given_twice_and_too_many(self):
        self.assertEqual(self.refused(commits=[]), "bad_input")
        self.assertEqual(self.refused(commits=[SHA, SHA]), "bad_input")
        many = [f"{i:040x}" for i in range(1, h.MAX_COMMITS + 2)]
        self.assertEqual(self.refused(commits=many), "too_many_commits")
        self.assertIsNone(self.ok(commits=many[: h.MAX_COMMITS]))

    def test_the_commits_are_split_on_any_blank(self):
        self.assertEqual(h.split_commits(f"{SHA}\n  {SHA[::-1]}\t"), [SHA, SHA[::-1]])
        self.assertEqual(h.split_commits(""), [])


class TestTheCommitsAgainstTheBaseAndTheBranch(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.repo = self._dir.name
        git(self.repo, "init", "-q", "-b", "version-15")
        self.first = commit(self.repo, {"a.txt": "1\n", "b.txt": "1\n"}, "first")
        self.base = commit(self.repo, {"a.txt": "2\n"}, "base")  # what the instance runs
        self.fix1 = commit(self.repo, {"POS/src/x.js": "fix 1\n"}, "fix 1")
        self.fix2 = commit(self.repo, {"pkg/y.py": "fix 2\n"}, "fix 2")
        self.after = commit(self.repo, {"c.txt": "later\n"}, "later work, not in the fix")
        git(self.repo, "update-ref", "refs/remotes/origin/version-15", self.after)
        self.ref = "origin/version-15"

    def check(self, base=None, commits=None):
        return h.check_commits(self.repo, base or self.base, commits or [self.fix1, self.fix2], self.ref)

    def refused(self, **kw):
        with self.assertRaises(h.Refusal) as caught:
            self.check(**kw)
        return caught.exception.code

    def test_a_fix_on_the_branch_after_the_base_passes(self):
        self.assertIsNone(self.check())

    def test_a_base_github_does_not_know_is_refused(self):
        self.assertEqual(self.refused(base="f" * 40), "unknown_base")

    def test_a_commit_that_is_not_a_commit_here(self):
        self.assertEqual(self.refused(commits=["e" * 40]), "unknown_commit")

    def test_a_base_off_the_branch_is_refused(self):
        git(self.repo, "checkout", "-q", "--detach", self.first)
        island = commit(self.repo, {"island.txt": "x\n"}, "an island")
        self.assertEqual(self.refused(base=island), "base_off_branch")

    def test_a_commit_off_the_branch_is_refused(self):
        git(self.repo, "checkout", "-q", "--detach", self.base)
        stray = commit(self.repo, {"stray.txt": "x\n"}, "tried nowhere")
        self.assertEqual(self.refused(commits=[stray]), "not_on_branch")

    def test_a_commit_already_in_the_base_is_refused(self):
        self.assertEqual(self.refused(commits=[self.first]), "already_in_base")

    def test_a_merge_commit_is_refused(self):
        git(self.repo, "checkout", "-q", "--detach", self.after)
        git(self.repo, "checkout", "-q", "-b", "side", self.base)
        side = commit(self.repo, {"side.txt": "x\n"}, "side")
        git(self.repo, "checkout", "-q", "--detach", self.after)
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", side)
        merge = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/version-15", merge)
        self.assertEqual(self.refused(commits=[merge]), "merge_commit")

    def test_a_dependency_or_a_patch_is_refused(self):
        for name in ("package.json", "POS/yarn.lock", "app/patches.txt", "requirements-dev.txt", "pyproject.toml"):
            git(self.repo, "checkout", "-q", "--detach", self.after)
            tip = commit(self.repo, {name: "x\n"}, f"touches {name}")
            git(self.repo, "update-ref", "refs/remotes/origin/version-15", tip)
            self.assertEqual(self.refused(commits=[tip]), "dependency_or_patch", name)

    def test_what_the_commits_touch(self):
        self.assertTrue(h.touches(self.repo, [self.fix1], ["POS/src/"]))
        self.assertFalse(h.touches(self.repo, [self.fix2], ["POS/src/"]))
        self.assertTrue(h.touches(self.repo, [self.fix2], ["POS/src/", "pkg/y.py"]))
        self.assertFalse(h.touches(self.repo, [self.fix1], ["POS/sr"]), "a folder is named with its slash")


class TestTheCherryPick(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.repo = self._dir.name
        git(self.repo, "init", "-q", "-b", "version-15")
        self.base = commit(self.repo, {"a.txt": "1\n", "b.txt": "1\n"}, "base")
        self.other = commit(self.repo, {"b.txt": "2\n"}, "work the instance does not have")
        self.fix = commit(self.repo, {"a.txt": "fixed\n"}, "fix")
        self.conflicting = commit(self.repo, {"b.txt": "3\n"}, "builds on the work the base lacks")

    def test_the_fix_is_laid_on_the_base_and_not_on_what_came_between(self):
        tip = h.apply_commits(self.repo, self.base, [self.fix])
        self.assertNotEqual(tip, self.fix)
        self.assertEqual(git(self.repo, "rev-parse", f"{tip}~1"), self.base)
        self.assertEqual(Path(self.repo, "a.txt").read_text(), "fixed\n")
        self.assertEqual(Path(self.repo, "b.txt").read_text(), "1\n", "the work between the base and the fix is not carried")
        self.assertIn(f"(cherry picked from commit {self.fix})", git(self.repo, "log", "-1", "--format=%B", tip))
        self.assertEqual(git(self.repo, "log", "-1", "--format=%cn", tip), "github-actions[bot]")

    def test_a_commit_that_builds_on_missing_work_is_a_named_refusal_and_leaves_the_base(self):
        with self.assertRaises(h.Refusal) as caught:
            h.apply_commits(self.repo, self.base, [self.fix, self.conflicting])
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


class TestTheWitness(unittest.TestCase):
    def test_the_fingerprint_is_taken_out_of_a_name(self):
        for built, expected in (
            ("pos_next/public/pos/assets/POSSale-CF7NL2OR.js", "pos_next/public/pos/assets/POSSale-<hash>.js"),
            ("pos_next/public/pos/assets/Inter-Black-CAR6omXH.woff", "pos_next/public/pos/assets/Inter-Black-<hash>.woff"),
            ("pos_next/public/pos/assets/ShiftOpeningDialog-adTS-mg4.js", "pos_next/public/pos/assets/ShiftOpeningDialog-<hash>.js"),
            ("pos_next/public/pos/sw.js", "pos_next/public/pos/sw.js"),
            ("pos_next/public/pos/version.json", "pos_next/public/pos/version.json"),
            ("pos_next/public/pos/workers/offline.worker.js", "pos_next/public/pos/workers/offline.worker.js"),
        ):
            self.assertEqual(h.normalize_name(built), expected)

    def test_two_builds_with_other_fingerprints_give_the_same_set(self):
        a = [h.normalize_name(p) for p in ("x/index-AAAAAAAA.js", "x/Login-BBBBBBBB.js", "x/sw.js")]
        b = [h.normalize_name(p) for p in ("x/index-CCCCCCCC.js", "x/Login-DDDDDDDD.js", "x/sw.js")]
        self.assertEqual(h.compare_filesets(a, b), ([], []))

    def test_a_missing_or_an_extra_file_is_a_difference_and_so_is_a_count(self):
        only_built, only_committed = h.compare_filesets(
            ["x/a-<hash>.js", "x/b-<hash>.js", "x/b-<hash>.js"], ["x/a-<hash>.js", "x/b-<hash>.js", "x/c.js"]
        )
        self.assertEqual((only_built, only_committed), (["x/b-<hash>.js"], ["x/c.js"]))

    def test_the_files_of_a_build_on_disk_and_of_a_revision(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "version-15")
            base = commit(
                repo,
                {"pos_next/public/pos/assets/A-AAAAAAAA.js": "1", "pos_next/www/pos.html": "1", "README.md": "1"},
                "base",
            )
            paths = ["pos_next/public/pos/", "pos_next/www/pos.html", "pos_next/www/pos-sw.js"]
            Path(repo, "pos_next/public/pos/assets/A-AAAAAAAA.js").rename(Path(repo, "pos_next/public/pos/assets/A-ZZZZZZZZ.js"))
            Path(repo, "pos_next/www/pos-sw.js").write_text("sw")
            self.assertEqual(
                h.fileset_of_directory(repo, paths),
                ["pos_next/public/pos/assets/A-<hash>.js", "pos_next/www/pos-sw.js", "pos_next/www/pos.html"],
            )
            self.assertEqual(
                h.fileset_of_revision(repo, base, paths),
                ["pos_next/public/pos/assets/A-<hash>.js", "pos_next/www/pos.html"],
            )


class TestTheTag(unittest.TestCase):
    def test_the_first_tag_of_a_base_is_one(self):
        self.assertEqual(h.next_tag("pos_next", SHA, []), "hotfix/pos_next/0123456-1")

    def test_the_next_rank_follows_the_highest_one(self):
        refs = [
            "refs/tags/hotfix/pos_next/0123456-1",
            "refs/tags/hotfix/pos_next/0123456-1^{}",
            "refs/tags/hotfix/pos_next/0123456-3",
            "refs/tags/hotfix/pos_next/0123456-02",  # not ours
            "refs/tags/hotfix/pos_next/9999999-8",  # another base
            "refs/tags/hotfix/other_app/0123456-9",  # another app
            "refs/tags/v1.0",
        ]
        self.assertEqual(h.next_tag("pos_next", SHA, refs), "hotfix/pos_next/0123456-4")

    def test_the_notice_says_which_run_made_which_tag(self):
        self.assertEqual(
            h.notice_line("hotfix/pos_next/0123456-1", "a" * 40, "HF-0001", "123456789"),
            f"hotfix-overlay HF-0001 tag=hotfix/pos_next/0123456-1 sha={'a' * 40} run=123456789",
        )


class TestWhatTheBuildMayWrite(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.repo = self._dir.name
        git(self.repo, "init", "-q", "-b", "version-15")
        self.base = commit(self.repo, {"a.txt": "1\n"}, "base")

    def test_a_fix_commit_that_writes_the_build_is_refused(self):
        hand = commit(self.repo, {"pkg/public/pos/assets/x-AAAAAAAA.js": "evil\n"}, "writes the build by hand")
        git(self.repo, "update-ref", "refs/remotes/origin/version-15", hand)
        with self.assertRaises(h.Refusal) as caught:
            h.check_commits(self.repo, self.base, [hand], "origin/version-15", ["pkg/public/pos/", "pkg/www/pos.html"])
        self.assertEqual(caught.exception.code, "fix_writes_the_build")

    def test_a_fix_that_does_not_is_fit(self):
        fix = commit(self.repo, {"src/x.js": "ok\n"}, "a source")
        git(self.repo, "update-ref", "refs/remotes/origin/version-15", fix)
        self.assertIsNone(h.check_commits(self.repo, self.base, [fix], "origin/version-15", ["pkg/public/pos/"]))

    def test_a_symlink_is_not_a_file_of_a_fix(self):
        Path(self.repo, "link").symlink_to("../../../sites/prod.local/site_config.json")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "a link")
        with self.assertRaises(h.Refusal) as caught:
            h.check_modes(self.repo, self.base)
        self.assertEqual(caught.exception.code, "not_a_regular_file")

    def test_regular_files_pass_and_an_executable_too(self):
        Path(self.repo, "run.sh").write_text("#!/bin/sh\n")
        Path(self.repo, "run.sh").chmod(0o755)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "a script")
        self.assertIsNone(h.check_modes(self.repo, self.base))

    def test_the_build_version_holds_no_identifier_and_no_run(self):
        version = h.build_version(SHA, [SHA[::-1], "f" * 40])
        self.assertEqual(version, "hf-0123456-fffffff")
        self.assertNotIn("HF", version)

    def test_the_ranks_run_out_at_four_digits(self):
        refs = [f"refs/tags/hotfix/pos_next/0123456-{n}" for n in (1, 9999)]
        with self.assertRaises(h.Refusal):
            h.next_tag("pos_next", SHA, refs)


class TestWhatTheLogCarries(unittest.TestCase):
    def test_a_path_cannot_write_a_workflow_command(self):
        escaped = h.escape_command_data("a\n::add-mask::x\r100%")
        self.assertNotIn("\n", escaped)
        self.assertNotIn("\r", escaped)
        self.assertEqual(escaped, "a%0A::add-mask::x%0D100%25")


class TestTheCommandLine(unittest.TestCase):
    def run_cli(self, argv, **env):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return h.main(argv)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_a_bad_input_ends_the_job(self):
        code = self.run_cli(
            ["check", "--repo", "."], APP="pos_next", BASE="main", COMMITS=SHA, HOTFIX_ID="HF-0001", BRANCH="version-15"
        )
        self.assertEqual(code, 1)

    def test_a_value_with_a_shell_meaning_never_gets_past_the_check(self):
        for hostile in ("$(id)", "`id`", "a; rm -rf /", "x\nHF-0001"):
            code = self.run_cli(
                ["check", "--repo", "."], APP="pos_next", BASE=SHA, COMMITS=SHA, HOTFIX_ID=hostile, BRANCH="version-15"
            )
            self.assertEqual(code, 1, hostile)


if __name__ == "__main__":
    unittest.main()
