#!/usr/bin/env python3
"""The checks and the git steps of `.github/workflows/hotfix-overlay.yml` (neoffice-maintenance#3237).

A targeted fix is laid on the base an instance already runs: the CI cherry-picks the commits of the fix (already in the deploy branch,
tried on osiris) onto that base, rebuilds the front end if they touch it, and publishes the result as a TAG, `hotfix/<app>/<B7>-<n>`,
never a branch. This script is what the workflow runs; every decision lives here, where it is tested, and the workflow only calls it.

What comes in is a base, some commits and an opaque identifier: hashes and `HF-0001`, validated with the same expressions the hub
reads a tag back with (`neoffice_devops/cloud/hotfix/tags.py`), and NEVER a name of an instance, of a host or of a client: the tags
and this repository are public. A value is never put in a shell command: the workflow hands it over in the environment.

    check          validate the inputs, then the commits against the base and the deploy branch (named refusals)
    apply          cherry-pick -x the commits onto the base
    touches        say whether the commits touch a path (the front end's sources)
    fileset        the names of the files of the build, or of a revision, with the fingerprints of the bundler taken out
    compare        the witness: the build of the base alone must give the files the base committed
    next-tag       the name of the tag: `hotfix/<app>/<B7>-<n>`, n the next free rank
    notice         the line the workflow writes for the hub to read back (which run made which tag)

Every refusal is `refused: <code>: <why>` on the error output and exit code 1: `check`, `apply`, `compare` and `next-tag` end the job.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import os
import re
import subprocess
import sys

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
HOTFIX_ID_RE = re.compile(r"HF-[0-9]{4,6}\Z")
APP_RE = re.compile(r"[a-z][a-z0-9_]{1,39}\Z")
BRANCH_RE = re.compile(r"version-[0-9]{2}\Z")
TAG_PREFIX = "hotfix/"
# The size cap of the hub (cloud/hotfix/eligibility.py: MAX_COMMITS): a fix above it is a wave, so no CI minute is spent on it.
MAX_COMMITS = 10

# A fix that changes what is installed cannot be laid on a base whose dependencies the build installs once.
DENIED_NAMES = (
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "poetry.lock",
    "requirements*.txt",
    "patches.txt",
)

# The bundler puts 8 characters of base64url into the name of what it writes: `POSSale-CF7NL2OR.js`, `Inter-Black-CAR6omXH.woff`.
FINGERPRINT_RE = re.compile(r"-[A-Za-z0-9_-]{8}(?=\.[A-Za-z0-9]+\Z)")


class Refusal(Exception):
    def __init__(self, code: str, why: str):
        super().__init__(f"{code}: {why}")
        self.code, self.why = code, why


def git(repo: str, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, **(env or {})},
    )


def split_commits(raw: str) -> list[str]:
    return [c for c in re.split(r"\s+", (raw or "").strip()) if c]


def validate_inputs(app: str, base: str, commits: list[str], hotfix_id: str, branch: str) -> None:
    """Refuse an input that is not exactly what it should be. These values come from a person's hand, through a dispatch."""
    if not APP_RE.match(app or ""):
        raise Refusal("bad_input", "the app is not a lower-case Python package name")
    if not SHA_RE.match(base or ""):
        raise Refusal("bad_input", "the base is not a full lower-case commit hash")
    if not commits:
        raise Refusal("bad_input", "no commit of the fix was given")
    for commit in commits:
        if not SHA_RE.match(commit):
            raise Refusal("bad_input", "a commit of the fix is not a full lower-case commit hash")
    if len(set(commits)) != len(commits):
        raise Refusal("bad_input", "a commit of the fix is given twice")
    if len(commits) > MAX_COMMITS:
        raise Refusal("too_many_commits", f"{len(commits)} commits: a targeted fix holds {MAX_COMMITS} at most, a wave carries more")
    if not HOTFIX_ID_RE.match(hotfix_id or ""):
        raise Refusal("bad_input", "the identifier is not of the form HF-0001")
    if not BRANCH_RE.match(branch or ""):
        raise Refusal("bad_input", "the deploy branch is not of the form version-15")


def _has_commit(repo: str, sha: str) -> bool:
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def check_commits(repo: str, base: str, commits: list[str], branch_ref: str) -> None:
    """The base is on the deploy branch; every commit is on it too, is not in the base already, and applies as a plain commit."""
    if not _has_commit(repo, base):
        raise Refusal("unknown_base", "the base is not a commit of this repository: GitHub does not know what the instance runs")
    if not _is_ancestor(repo, base, branch_ref):
        raise Refusal("base_off_branch", "the base is not on the deploy branch: a wave puts the instance back on it first")
    for commit in commits:
        short = commit[:7]
        if not _has_commit(repo, commit):
            raise Refusal("unknown_commit", f"{short} is not a commit of this repository")
        if not _is_ancestor(repo, commit, branch_ref):
            raise Refusal("not_on_branch", f"{short} is not on the deploy branch: a fix is laid only after it is there and tried")
        if _is_ancestor(repo, commit, base):
            raise Refusal("already_in_base", f"{short} is already in the base: nothing to lay")
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).stdout.split()
        if len(parents) != 2:
            raise Refusal("merge_commit", f"{short} is not a plain commit with one parent")
        for path in _files(repo, commit):
            name = path.rsplit("/", 1)[-1]
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in DENIED_NAMES):
                raise Refusal("dependency_or_patch", f"{short} changes {path}: a dependency or a patch goes in a wave")


def _files(repo: str, commit: str) -> list[str]:
    out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit).stdout
    return [p for p in out.split("\0") if p]


def touches(repo: str, commits: list[str], prefixes: list[str]) -> bool:
    """Whether one of the commits changes a path under one of these prefixes (a folder ends with `/`, a file is named whole)."""
    return any(
        path == prefix or (prefix.endswith("/") and path.startswith(prefix))
        for commit in commits
        for path in _files(repo, commit)
        for prefix in prefixes
    )


def apply_commits(repo: str, base: str, commits: list[str]) -> str:
    """Detach at the base and cherry-pick -x the commits, in the given order; the tip is returned. A conflict leaves the repository at the base."""
    bot = {
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }
    git(repo, "checkout", "--quiet", "--detach", base)
    for commit in commits:
        picked = git(repo, "cherry-pick", "-x", commit, check=False, env=bot)
        if picked.returncode != 0:
            git(repo, "cherry-pick", "--abort", check=False)
            git(repo, "reset", "--hard", "--quiet", base)
            raise Refusal("conflict", f"{commit[:7]} does not apply on the base: this fix goes in a wave")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def normalize_name(path: str) -> str:
    """The name of a built file without its fingerprint: `a/POSSale-CF7NL2OR.js` becomes `a/POSSale-<hash>.js`."""
    return FINGERPRINT_RE.sub("-<hash>", path)


def fileset_of_directory(root: str, paths: list[str]) -> list[str]:
    """The files under these paths of `root` (a folder ends with `/`), as repository paths, fingerprints taken out, sorted."""
    found = []
    for entry in paths:
        full = os.path.join(root, entry)
        if entry.endswith("/"):
            for here, _dirs, names in os.walk(full):
                found += [os.path.relpath(os.path.join(here, n), root) for n in names]
        elif os.path.isfile(full):
            found.append(entry)
    return sorted(normalize_name(p) for p in found)


def fileset_of_revision(repo: str, rev: str, paths: list[str]) -> list[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", "-z", rev, "--", *paths).stdout
    return sorted(normalize_name(p) for p in out.split("\0") if p)


def compare_filesets(built: list[str], committed: list[str]) -> tuple[list[str], list[str]]:
    """`(only_built, only_committed)` as multisets: a count that differs is a difference."""
    a, b = collections.Counter(built), collections.Counter(committed)
    return sorted((a - b).elements()), sorted((b - a).elements())


def next_tag(app: str, base: str, existing_refs: list[str]) -> str:
    """`hotfix/<app>/<B7>-<n>`, n one above the highest rank already published for this app and this base."""
    head = f"{TAG_PREFIX}{app}/{base[:7]}-"
    ranks = []
    for ref in existing_refs:
        name = ref.split("refs/tags/", 1)[-1].removesuffix("^{}")
        match = re.fullmatch(re.escape(head) + r"([1-9][0-9]{0,3})", name)
        if match:
            ranks.append(int(match.group(1)))
    return f"{head}{max(ranks, default=0) + 1}"


def notice_line(tag: str, tip: str, hotfix_id: str, run_id: str) -> str:
    """The line the hub reads back to know WHICH run made WHICH tag: it is written as a notice of the run, and in the tag's message."""
    return f"hotfix-overlay {hotfix_id} tag={tag} sha={tip} run={run_id}"


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def escape_command_data(text: str) -> str:
    """What a workflow command carries is escaped as the runner expects: a path named in a refusal is the repository's, not ours."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
    print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "apply", "touches"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
    sub.choices["check"].add_argument("--branch-ref", default=None, help="default: origin/<BRANCH>")
    sub.choices["touches"].add_argument("--prefixes", default=None, help="default: the FRONT_SOURCES environment variable")
    for name in ("fileset", "compare", "next-tag", "notice"):
        sub.add_parser(name)
    sub.choices["fileset"].add_argument("--repo", default=".")
    sub.choices["fileset"].add_argument("--rev", default=None, help="a revision (what was committed); default: the files on disk")
    sub.choices["fileset"].add_argument("--paths", default=None, help="default: the ARTIFACT_PATHS environment variable")
    sub.choices["compare"].add_argument("built")
    sub.choices["compare"].add_argument("committed")
    sub.choices["next-tag"].add_argument("--repo", default=".")
    sub.choices["notice"].add_argument("--tag", required=True)
    sub.choices["notice"].add_argument("--sha", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            commits = split_commits(_env("COMMITS"))
            validate_inputs(_env("APP"), _env("BASE"), commits, _env("HOTFIX_ID"), _env("BRANCH"))
            check_commits(args.repo, _env("BASE"), commits, args.branch_ref or f"origin/{_env('BRANCH')}")
            _output("commit_count", str(len(commits)))
            return 0
        if args.command == "apply":
            tip = apply_commits(args.repo, _env("BASE"), split_commits(_env("COMMITS")))
            _output("tip", tip)
            return 0
        if args.command == "touches":
            prefixes = _lines(args.prefixes if args.prefixes is not None else _env("FRONT_SOURCES"))
            _output("front_touched", "true" if touches(args.repo, split_commits(_env("COMMITS")), prefixes) else "false")
            return 0
        if args.command == "fileset":
            paths = _lines(args.paths if args.paths is not None else _env("ARTIFACT_PATHS"))
            names = fileset_of_revision(args.repo, args.rev, paths) if args.rev else fileset_of_directory(args.repo, paths)
            print("\n".join(names))
            return 0
        if args.command == "compare":
            with open(args.built, encoding="utf-8") as built, open(args.committed, encoding="utf-8") as committed:
                only_built, only_committed = compare_filesets(_lines(built.read()), _lines(committed.read()))
            if only_built or only_committed:
                for name in only_built[:40]:
                    print(f"only in the build of the base: {name}", file=sys.stderr)
                for name in only_committed[:40]:
                    print(f"only in what the base committed: {name}", file=sys.stderr)
                raise Refusal(
                    "build_not_reproducible",
                    "the build of the base alone does not give the files the base committed: a wave carries this fix",
                )
            print("witness: the build of the base gives the files the base committed")
            return 0
        if args.command == "next-tag":
            raw = git(args.repo, "ls-remote", "--tags", "origin", f"refs/tags/{TAG_PREFIX}{_env('APP')}/*").stdout
            refs = [line.split("\t", 1)[1] for line in raw.splitlines() if "\t" in line]
            print(next_tag(_env("APP"), _env("BASE"), refs))
            return 0
        if args.command == "notice":
            print(notice_line(args.tag, args.sha, _env("HOTFIX_ID"), _env("RUN_ID")))
            return 0
    except Refusal as refusal:
        print(f"refused: {refusal.code}: {refusal.why}", file=sys.stderr)
        print(f"::error title=hotfix overlay refused ({refusal.code})::{escape_command_data(refusal.why)}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
