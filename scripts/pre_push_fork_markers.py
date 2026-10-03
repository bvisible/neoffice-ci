#!/usr/bin/env python3
# //// Neoffice — pre-push fork markers: this line is how the installer recognises its own hook.
"""A `pre-push` hook for the Neoffice forks: refuse a push to `version-15` that carries unmarked hunks.

The Fork markers workflow of a fork turns red when a pushed range changes upstream code without a
`//// Neoffice — <why>` marker, and the pass that writes the missing markers needs an AI account that
can be out of quota for days (it was, 2026-10-01 to 2026-10-05: every unmarked push of the wiki's
reader went red, seven in a row one morning). This runs the SAME checker, over the SAME range, before
the push leaves the machine, so the marker is written while the commit is still in hand.

  Install, once per clone (worktrees share their clone's hooks):
      python3 pre_push_fork_markers.py --install /path/to/the/clone
  Skip it once:  git push --no-verify   or   NEOFFICE_SKIP_MARKERS_CHECK=1 git push

It is a guard, not a gate on the tooling: when it cannot do its job (no network and no cached checker,
a merge in the range, a checker that crashes or takes too long) it says so and lets the push go, the
CI being the net underneath. It refuses only when the checker itself reports unmarked hunks.

The range is the workflow's: from the last clean state (`refs/fork-markers/clean`, fetched from the
remote) when it is an ancestor of what is pushed, else from what the remote already had. The upstream
base is taken from the `upstream` remote's branches already present in the clone (no fetch of them).
The checker is `scripts/fork_markers.py` of bvisible/neoffice-ci, read from `main` like the workflow
does (NEOFFICE_FORK_MARKERS_SCRIPT points to a local copy instead: tests, offline).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys

BRANCH = "refs/heads/version-15"
ZERO = "0" * 40
RAW = "https://raw.githubusercontent.com/bvisible/neoffice-ci/main/scripts/fork_markers.py"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "neoffice", "fork_markers.py")
OWN_MARK = "pre-push fork markers: this line is how the installer recognises its own hook"
CHECK_TIMEOUT = 180  # seconds: a huge range is the CI's job, not a reason to hold a push for minutes

HELP = """
Refused: {n} hunk(s) of this push change upstream code with no `//// Neoffice` marker, so the Fork
markers run would turn red. Write the reason (what upstream did, what it broke, what we do instead)
in a comment within 3 lines above each hunk listed above:
    JS / TS / Vue <script>   //// Neoffice — <why>
    Python / YAML            # //// Neoffice — <why>
    Jinja template           {{# //// Neoffice — <why> #}}
    HTML / Vue <template>    <!-- //// Neoffice — <why> -->
A file that exists nowhere upstream needs one `//// Neoffice — added file (no upstream equivalent)`
header in its first lines instead. To push anyway: git push --no-verify
"""


def git(args, cwd, timeout=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def parse_refs(text):
    """The lines git feeds a pre-push hook: `<local ref> <local sha> <remote ref> <remote sha>`."""
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append(tuple(parts))
    return refs


def is_ancestor(repo, older, newer):
    return git(["merge-base", "--is-ancestor", older, newer], repo).returncode == 0


def rev_parse(repo, name):
    done = git(["rev-parse", "-q", "--verify", name], repo)
    return done.stdout.strip() if done.returncode == 0 else ""


def choose_base(repo, head, remote_sha, clean_sha):
    """Where the checked range starts: the workflow's rule, from the series and not only the push."""
    if clean_sha and clean_sha != head and is_ancestor(repo, clean_sha, head):
        return clean_sha
    if remote_sha != ZERO and git(["cat-file", "-e", remote_sha + "^{commit}"], repo).returncode == 0 and is_ancestor(repo, remote_sha, head):
        return remote_sha
    return rev_parse(repo, head + "~1")


def upstream_base(repo, head):
    """The merge-base with the upstream's branches that leaves the fewest commits of ours on top (the
    workflow's choice), from the refs already in the clone: no network. Empty when there is none."""
    listed = git(["for-each-ref", "--format=%(refname)", "refs/remotes/upstream"], repo)
    best = None
    for ref in listed.stdout.split():
        mb = git(["merge-base", head, ref], repo)
        if mb.returncode != 0 or not mb.stdout.strip():
            continue
        count = git(["rev-list", "--count", f"{mb.stdout.strip()}..{head}"], repo)
        if count.returncode == 0 and (best is None or int(count.stdout) < best[0]):
            best = (int(count.stdout), mb.stdout.strip())
    return best[1] if best else ""


def checker_path():
    """The checker to run: a local copy when told so, else the one the workflow reads, else the last one
    this machine fetched. None when there is no way to get one."""
    forced = os.environ.get("NEOFFICE_FORK_MARKERS_SCRIPT")
    if forced:
        return forced if os.path.isfile(forced) else None
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".part"
    try:
        done = subprocess.run(["curl", "-fsSL", "--max-time", "10", "-o", tmp, RAW], capture_output=True)
        if done.returncode == 0 and os.path.getsize(tmp) > 1000:
            shutil.move(tmp, CACHE)
    except (OSError, subprocess.SubprocessError):
        pass
    return CACHE if os.path.isfile(CACHE) else None


def say(text):
    print(f"pre-push fork markers: {text}", file=sys.stderr)


def check_push(repo, remote, head, remote_sha):
    """True when the push may go. Refuses (False) only on a count of unmarked hunks from the checker."""
    git(["fetch", "-q", remote, "+refs/fork-markers/clean:refs/fork-markers/clean"], repo, timeout=20)
    base = choose_base(repo, head, remote_sha, rev_parse(repo, "refs/fork-markers/clean"))
    if not base:
        return True
    merges = git(["rev-list", "--merges", f"{base}..{head}"], repo)
    if merges.stdout.strip():
        say("a merge is in the range, which the CI measures from the upstream side: not checked here")
        return True
    script = checker_path()
    if not script:
        say("no checker to run (offline and none cached): not checked here, the CI will")
        return True
    command = [sys.executable, script, "check", "--repo", repo, "--base", base, "--head", head]
    ub = upstream_base(repo, head)
    if ub:
        command += ["--upstream-base", ub]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired:
        say(f"the checker took more than {CHECK_TIMEOUT}s: not checked here, the CI will")
        return True
    found = re.search(r"(\d+) unmarked hunk\(s\) in ", done.stdout)
    if not found:
        say("the checker gave no verdict (" + (done.stderr.strip().splitlines() or ["no output"])[-1][:120] + "): not checked here")
        return True
    count = int(found.group(1))
    if count == 0:
        return True
    print(done.stdout.rstrip(), file=sys.stderr)
    print(HELP.format(n=count), file=sys.stderr)
    return False


def install(args):
    if not args:
        print("usage: pre_push_fork_markers.py --install /path/to/a/clone", file=sys.stderr)
        return 2
    common = git(["rev-parse", "--git-common-dir"], args[0])
    if common.returncode != 0:
        print(f"{args[0]} is not a git clone", file=sys.stderr)
        return 2
    hooks = os.path.join(os.path.abspath(os.path.join(args[0], common.stdout.strip())), "hooks")
    os.makedirs(hooks, exist_ok=True)
    target = os.path.join(hooks, "pre-push")
    if os.path.exists(target) and OWN_MARK not in open(target, encoding="utf-8", errors="replace").read():
        print(f"{target} exists and is not this hook: left alone", file=sys.stderr)
        return 1
    shutil.copyfile(os.path.abspath(__file__), target)
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed: {target}")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "--install":
        return install(argv[2:])
    if os.environ.get("NEOFFICE_SKIP_MARKERS_CHECK"):
        return 0
    remote = argv[1] if len(argv) > 1 else "origin"
    top = git(["rev-parse", "--show-toplevel"], os.getcwd())
    repo = top.stdout.strip() if top.returncode == 0 else os.getcwd()
    for _local_ref, local_sha, remote_ref, remote_sha in parse_refs(sys.stdin.read()):
        if remote_ref != BRANCH or local_sha == ZERO:
            continue
        try:
            if not check_push(repo, remote, local_sha, remote_sha):
                return 1
        except Exception as error:  # noqa: BLE001 — a guard that crashes must not become a wall
            say(f"could not check ({type(error).__name__}: {error}): not checked here, the CI will")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
