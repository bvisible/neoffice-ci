"""pre_push_fork_markers.py on throwaway repositories, with a real `git push`.
Run: python3 -m unittest scripts/test_pre_push_fork_markers.py"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pre_push_fork_markers as hook  # noqa: E402

CHECKER = os.path.join(HERE, "fork_markers.py")
BASE_CODE = "def price(item):\n    return item.rate\n\n\ndef label(item):\n    return item.name\n"
CHANGED = BASE_CODE.replace("return item.rate", "return item.rate * 2")
MARKED = BASE_CODE.replace("    return item.rate", "    # //// Neoffice — doubled, the shop quotes per pair\n    return item.rate * 2")


def sh(*args, cwd, env=None, check=True):
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and done.returncode != 0:
        raise AssertionError(f"{args} failed: {done.stdout}{done.stderr}")
    return done


class Repos(unittest.TestCase):
    """A bare `origin`, a clone `work` with the hook installed, and a first commit already pushed."""

    def setUp(self):
        root = tempfile.mkdtemp()
        self.origin = os.path.join(root, "origin.git")
        self.work = os.path.join(root, "work")
        sh("git", "init", "-q", "--bare", "-b", "version-15", self.origin, cwd=root)
        sh("git", "init", "-q", "-b", "version-15", self.work, cwd=root)
        for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
            sh("git", "config", key, value, cwd=self.work)
        sh("git", "remote", "add", "origin", self.origin, cwd=self.work)
        self.commit(BASE_CODE, "base")
        self.install()
        self.push()  # the very first push has no range to check

    def install(self):
        sh(sys.executable, os.path.join(HERE, "pre_push_fork_markers.py"), "--install", self.work, cwd=self.work)

    def commit(self, code, message="step"):
        with open(os.path.join(self.work, "mod.py"), "w") as handle:
            handle.write(code)
        sh("git", "add", "mod.py", cwd=self.work)
        sh("git", "commit", "-q", "-m", message, cwd=self.work)

    def push(self, *args, env=None, target="version-15"):
        # the checker is the repository's own copy: no network in a test
        merged = {"NEOFFICE_FORK_MARKERS_SCRIPT": CHECKER, **(env or {})}
        return sh("git", "push", "-q", *args, "origin", f"HEAD:{target}", cwd=self.work, env=merged, check=False)


class TestThePush(Repos):
    def test_an_unmarked_change_to_the_deploy_branch_is_refused(self):
        self.commit(CHANGED)
        done = self.push()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("1 unmarked hunk(s)", done.stderr)
        self.assertIn("mod.py", done.stderr)
        self.assertIn("--no-verify", done.stderr)
        self.assertEqual(sh("git", "rev-parse", "version-15", cwd=self.origin).stdout.strip() != sh("git", "rev-parse", "HEAD", cwd=self.work).stdout.strip(), True, "nothing reached the remote")

    def test_the_same_change_with_its_marker_goes_through(self):
        self.commit(MARKED)
        self.assertEqual(self.push().returncode, 0)

    def test_another_branch_is_not_checked(self):
        self.commit(CHANGED)
        self.assertEqual(self.push(target="some-feature").returncode, 0)

    def test_no_verify_skips_it_as_git_says(self):
        self.commit(CHANGED)
        self.assertEqual(self.push("--no-verify").returncode, 0)

    def test_the_environment_switch_skips_it(self):
        self.commit(CHANGED)
        self.assertEqual(self.push(env={"NEOFFICE_SKIP_MARKERS_CHECK": "1"}).returncode, 0)

    def test_a_checker_that_cannot_be_found_never_blocks_a_push(self):
        self.commit(CHANGED)
        done = self.push(env={"NEOFFICE_FORK_MARKERS_SCRIPT": "/nonexistent/fork_markers.py"})
        self.assertEqual(done.returncode, 0)
        self.assertIn("no checker to run", done.stderr)

    def test_a_checker_that_crashes_never_blocks_a_push(self):
        broken = os.path.join(tempfile.mkdtemp(), "fork_markers.py")
        with open(broken, "w") as handle:
            handle.write("raise SystemExit('boom')\n")
        self.commit(CHANGED)
        done = self.push(env={"NEOFFICE_FORK_MARKERS_SCRIPT": broken})
        self.assertEqual(done.returncode, 0)
        self.assertIn("no verdict", done.stderr)

    def test_the_range_starts_at_the_last_clean_state_not_only_at_the_push(self):
        # the series, not the push: an earlier unmarked commit already on the remote keeps the next push red
        self.commit(CHANGED, "unmarked, pushed past the hook")
        self.assertEqual(self.push("--no-verify").returncode, 0)
        sh("git", "update-ref", "refs/fork-markers/clean", "HEAD~1", cwd=self.origin)
        self.commit(CHANGED + "\n\ndef other():\n    return 1\n", "a later push that is fine in itself")
        done = self.push()
        self.assertNotEqual(done.returncode, 0, "the earlier hunk is still in the range")
        self.assertIn("unmarked hunk(s)", done.stderr)


class TestTheParts(Repos):
    def test_the_hook_input_is_read_line_by_line(self):
        text = "refs/heads/a aaaa refs/heads/version-15 bbbb\nnot a ref\n\n"
        self.assertEqual(hook.parse_refs(text), [("refs/heads/a", "aaaa", "refs/heads/version-15", "bbbb")])

    def test_the_base_prefers_the_clean_state_then_the_remote_then_the_parent(self):
        first = sh("git", "rev-parse", "HEAD", cwd=self.work).stdout.strip()
        self.commit(CHANGED, "second")
        self.commit(MARKED, "third")
        head = sh("git", "rev-parse", "HEAD", cwd=self.work).stdout.strip()
        parent = sh("git", "rev-parse", "HEAD~1", cwd=self.work).stdout.strip()
        self.assertEqual(hook.choose_base(self.work, head, hook.ZERO, first), first)
        self.assertEqual(hook.choose_base(self.work, head, parent, ""), parent)
        self.assertEqual(hook.choose_base(self.work, head, hook.ZERO, ""), parent)
        self.assertEqual(hook.choose_base(self.work, head, hook.ZERO, head), parent, "a clean state equal to the head is no range")

    def test_the_upstream_base_is_the_merge_base_leaving_the_fewest_commits_of_ours(self):
        base = sh("git", "rev-parse", "HEAD", cwd=self.work).stdout.strip()
        sh("git", "update-ref", "refs/remotes/upstream/develop", base, cwd=self.work)
        self.commit(CHANGED, "ours")
        self.assertEqual(hook.upstream_base(self.work, "HEAD"), base)
        sh("git", "update-ref", "-d", "refs/remotes/upstream/develop", cwd=self.work)
        self.assertEqual(hook.upstream_base(self.work, "HEAD"), "")


class TestTheInstaller(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        sh("git", "init", "-q", self.repo, cwd=self.repo)
        self.target = os.path.join(self.repo, ".git", "hooks", "pre-push")
        self.script = os.path.join(HERE, "pre_push_fork_markers.py")

    def test_it_installs_an_executable_copy(self):
        sh(sys.executable, self.script, "--install", self.repo, cwd=self.repo)
        self.assertTrue(os.access(self.target, os.X_OK))
        self.assertIn(hook.OWN_MARK, open(self.target).read())

    def test_it_can_be_run_again_over_its_own_hook(self):
        sh(sys.executable, self.script, "--install", self.repo, cwd=self.repo)
        sh(sys.executable, self.script, "--install", self.repo, cwd=self.repo)

    def test_it_never_overwrites_a_hook_that_is_not_its_own(self):
        os.makedirs(os.path.dirname(self.target), exist_ok=True)
        with open(self.target, "w") as handle:
            handle.write("#!/bin/sh\necho somebody else's hook\n")
        done = sh(sys.executable, self.script, "--install", self.repo, cwd=self.repo, check=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn("somebody else's hook", open(self.target).read())

    def test_it_refuses_a_path_that_is_not_a_clone(self):
        done = sh(sys.executable, self.script, "--install", tempfile.mkdtemp(), cwd=self.repo, check=False)
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
