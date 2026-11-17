#!/usr/bin/env python3
"""A commit that changes what the assistant reads or proposes names the site key that guards the change.

Customers work while we develop. A change of what the assistant reads, of what it proposes, or of what the booking
window does with it, leaves behind a site key that is off by default: with the keys off nothing changes for anybody,
and a test proves it. On 17.11.2026 a batch reached the fleet's branch that changed how a receipt is booked for every
customer, with no key at all; nobody had been asked to say which key guarded it.

In a repository that opted in (`.decision-paths.json` at its root, with `watched` paths and the day the rule starts,
`"key_line_since": "YYYY-MM-DD"`; without them this check does nothing), a commit that touches a watched path holds
one of these lines:

    Key: scan_split_by_rate
    Key: none, tests and translations only, reviewed by the pilot

- `Key: <name>`: the site key that guards the change. It must exist in the repository, written between quotes in a
  `.py`, `.js` or `.json` file: a key nobody reads guards nothing.
- `Key: none, <why nothing changes for a customer>, reviewed by <who>`: the reason in at least MIN_REASON characters.
- A commit already pushed without its line is not rewritten: a later commit repairs it with
  `Key-For: <its sha, 7 characters or more> <one of the two forms above, without the word Key:>`.

It cannot prove that the code changed really sits behind the key it names: only the test « keys off, nothing changes »
does. The line makes the author ask the question and leaves a trace a reader can check.
Merges are not judged (a merge of upstream brings their commits), nor a commit made before `key_line_since`.

The line is asked only where it means something: asked of every commit, it would be written without a thought. A
commit needs it when it changes the code of a watched path, so not for:
- a path watched for its word lists only: a watched entry that says `"key_line": false` (a file that mixes what the
  assistant decides with everything else); a file such an entry matches is exempt, whatever the other entries say;
- a test (`test_*.py`, `*.test.js`, a `tests/` folder);
- a change of comments only: every line added or removed in the file is blank or a line comment (`#` in Python,
  `//` in JavaScript). Code turned into a comment is not a comment: the line removed was code.

A message can be corrected before the commit, not after the push. So the gate before a commit (--message) refuses
from the first day, and a repository may give the pushed commits a period of warnings: with
`"key_line_blocks_from": "YYYY-MM-DD"`, a commit made before that day and found without its line is only said, as a
warning annotation (`::warning::`); a commit made from that day on is refused.

    check_key_line.py --message FILE [--files f ...] the gate before a commit: this message, and these files or,
                                                     without --files (a commit-msg hook), the staged ones
    check_key_line.py --base REV [--root DIR]        every commit of REV..HEAD (a push)
    check_key_line.py --days N [--root DIR]          every commit of the last N days (the default, 14): a commit
                                                     pushed without its line keeps the check red until it is repaired
                                                     (or is only warned about, before `key_line_blocks_from`)

Exit 1 when a commit is refused.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

CONFIG = ".decision-paths.json"
MIN_REASON = 20
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
KEY_NAME = re.compile(r"[a-z][a-z0-9_]{3,}")
NONE = re.compile(r"none\s*,\s*(?P<why>.+?)\s*,\s*reviewed by\s+(?P<who>\S.*)", re.IGNORECASE)
KEY_LINE = re.compile(r"^Key:\s*(?P<value>\S.*)$", re.MULTILINE)
REPAIR_LINE = re.compile(r"^Key-For:\s*(?P<sha>[0-9a-f]{7,40})\s+(?P<value>\S.*)$", re.MULTILINE)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    return done.stdout if done.returncode == 0 else ""


def is_test(name: str) -> bool:
    path = Path(name)
    return path.name.startswith("test_") or ".test." in path.name or "tests" in path.parts


def asks_for_the_line(config: dict, name: str) -> bool:
    """Whether a file is one whose code changes need the key line: watched, not for its lists only, not a test."""
    matching = [
        entry for entry in config.get("watched") or [] if fnmatch.fnmatchcase(name, str(entry.get("path") or ""))
    ]
    return bool(matching) and not any(entry.get("key_line") is False for entry in matching) and not is_test(name)


def comments_only(root: Path, name: str, revision: str | None) -> bool:
    """Whether what a commit (or, before a commit, the working tree against HEAD) changes in a file is comments only.

    Every line added or removed is blank or a line comment. A change with no line to read (a new file not yet
    followed, a renaming, a change of mode) is not taken for comments."""
    marks = {".py": "#", ".js": "//"}.get(Path(name).suffix)
    if not marks:
        return False
    diff = git(root, "show", "--format=", "-U0", revision, "--", name) if revision else git(
        root, "diff", "-U0", "HEAD", "--", name
    )
    changed = [
        line[1:].strip()
        for line in diff.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
    ]
    return bool(changed) and all(not line or line.startswith(marks) for line in changed)


def changes_code(config: dict, root: Path, names: list[str], revision: str | None) -> list[str]:
    """The files of a commit that make it need the key line."""
    return [
        name
        for name in names
        if name and asks_for_the_line(config, Path(name).as_posix()) and not comments_only(root, name, revision)
    ]


def key_exists(root: Path, key: str, revision: str | None) -> bool:
    """Whether the key is written between quotes in a source or a configuration file of the repository."""
    command = ["git", "-C", str(root), "grep", "-q", "-F", "-e", f'"{key}"', "-e", f"'{key}'"]
    command += ([revision] if revision else []) + ["--", "*.py", "*.js", "*.json"]
    return subprocess.run(command, capture_output=True, timeout=120).returncode == 0


def flaw(root: Path, value: str, revision: str | None) -> str | None:
    """Why this value of a key line is not accepted; None when it is."""
    value = value.strip()
    said_none = NONE.fullmatch(value)
    if said_none:
        if len(said_none.group("why").strip()) < MIN_REASON:
            return f"« none » says in at least {MIN_REASON} characters why nothing changes for a customer"
        return None
    if value.lower().startswith("none"):
        return "« none » is written `Key: none, <why nothing changes for a customer>, reviewed by <who>`"
    if not KEY_NAME.fullmatch(value):
        return f"« {value} » is not the name of a site key"
    if not key_exists(root, value, revision):
        return f"the key « {value} » is written nowhere in the repository: a key nobody reads guards nothing"
    return None


def verdict(root: Path, message: str, revision: str | None) -> str | None:
    """Why a commit message is refused; None when one of its key lines is accepted."""
    flaws = [flaw(root, found.group("value"), revision) for found in KEY_LINE.finditer(message)]
    if not flaws:
        return "no `Key:` line"
    return None if None in flaws else flaws[0]


def commits(root: Path, selection: list[str]) -> list[str]:
    return [sha for sha in git(root, "rev-list", "--no-merges", *selection).split() if sha]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--message", help="the file that holds the message of the commit about to be made")
    parser.add_argument("--files", nargs="*", default=[], help="with --message: the files (default: the staged)")
    parser.add_argument("--base", help="judge every commit of BASE..HEAD")
    parser.add_argument("--days", type=int, default=14, help="judge every commit of the last N days (default 14)")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    except OSError:
        print(f"no {CONFIG} in this repository: no commit is judged")
        return 0
    except ValueError as error:
        print(f"{CONFIG} is not valid JSON: {error}")
        return 1
    since = str(config.get("key_line_since") or "")
    if not DAY.fullmatch(since):
        print(f"{CONFIG} names no `key_line_since` day: the key line has not started here")
        return 0
    blocks_from = str(config.get("key_line_blocks_from") or "")
    if blocks_from and not DAY.fullmatch(blocks_from):
        print(f"{CONFIG}: `key_line_blocks_from` is a day (YYYY-MM-DD), not « {blocks_from} »")
        return 1

    if args.message:
        names = args.files or git(root, "diff", "--cached", "--name-only").split("\n")
        touched = changes_code(config, root, names, None)
        if not touched:
            return 0
        why = verdict(root, Path(args.message).read_text(encoding="utf-8"), None)
        if why:
            print(f"this commit touches a path where the assistant decides ({touched[0]}): {why}.")
            print("Add `Key: <site key>` or `Key: none, <why nothing changes for a customer>, reviewed by <who>`.")
        return 1 if why else 0

    selection = [f"{args.base}..HEAD"] if args.base else [f"--since={args.days} days ago", "HEAD"]
    judged = commits(root, selection)
    repairs = []
    for sha in judged:
        for found in REPAIR_LINE.finditer(git(root, "log", "-1", "--format=%B", sha)):
            if not flaw(root, found.group("value"), "HEAD"):
                repairs.append(found.group("sha"))
    refused = warned = 0
    for sha in reversed(judged):
        made = git(root, "log", "-1", "--format=%cs", sha).strip()
        if made < since:
            continue
        names = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).split("\n")
        touched = changes_code(config, root, names, sha)
        if not touched or any(sha.startswith(repair) for repair in repairs):
            continue
        why = verdict(root, git(root, "log", "-1", "--format=%B", sha), "HEAD")
        if why:
            subject = git(root, "log", "-1", "--format=%s", sha).strip()
            line = f"{sha[:10]} « {subject[:80]} » touches {touched[0]}: {why}."
            if blocks_from and made < blocks_from:
                warned += 1
                print(f"::warning::{line} A commit made from {blocks_from} on without its key line is refused.")
            else:
                refused += 1
                print(line)
    if warned:
        print(f"{warned} commit(s) made before {blocks_from} without their key line: said, not refused.")
    if refused:
        print(
            f"\n{refused} commit(s) without the key that guards them. A pushed commit is not rewritten: add, in a "
            "later commit, `Key-For: <sha> <site key>` or `Key-For: <sha> none, <why>, reviewed by <who>`."
        )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
