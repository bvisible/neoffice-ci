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

    check_key_line.py --message FILE [--files f ...] the gate before a commit: this message, and these files or,
                                                     without --files (a commit-msg hook), the staged ones
    check_key_line.py --base REV [--root DIR]        every commit of REV..HEAD (a push)
    check_key_line.py --days N [--root DIR]          every commit of the last N days (the default, 14): a commit
                                                     pushed without its line keeps the check red until it is repaired

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
KEY_NAME = re.compile(r"[a-z][a-z0-9_]{3,}")
NONE = re.compile(r"none\s*,\s*(?P<why>.+?)\s*,\s*reviewed by\s+(?P<who>\S.*)", re.IGNORECASE)
KEY_LINE = re.compile(r"^Key:\s*(?P<value>\S.*)$", re.MULTILINE)
REPAIR_LINE = re.compile(r"^Key-For:\s*(?P<sha>[0-9a-f]{7,40})\s+(?P<value>\S.*)$", re.MULTILINE)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    return done.stdout if done.returncode == 0 else ""


def is_watched(config: dict, name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, str(entry.get("path") or "")) for entry in config.get("watched") or [])


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
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        print(f"{CONFIG} names no `key_line_since` day: the key line has not started here")
        return 0

    if args.message:
        names = args.files or git(root, "diff", "--cached", "--name-only").split("\n")
        touched = [name for name in names if name and is_watched(config, Path(name).as_posix())]
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
    refused = 0
    for sha in reversed(judged):
        if git(root, "log", "-1", "--format=%cs", sha).strip() < since:
            continue
        names = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).split("\n")
        touched = [name for name in names if name and is_watched(config, name)]
        if not touched or any(sha.startswith(repair) for repair in repairs):
            continue
        why = verdict(root, git(root, "log", "-1", "--format=%B", sha), "HEAD")
        if why:
            refused += 1
            subject = git(root, "log", "-1", "--format=%s", sha).strip()
            print(f"{sha[:10]} « {subject[:80]} » touches {touched[0]}: {why}.")
    if refused:
        print(
            f"\n{refused} commit(s) without the key that guards them. A pushed commit is not rewritten: add, in a "
            "later commit, `Key-For: <sha> <site key>` or `Key-For: <sha> none, <why>, reviewed by <who>`."
        )
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
