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

In a file that mixes what the assistant books with everything else, the line can be asked for some functions only:
the watched entry names them, each with its reason,

    {"path": "app/mobile.py", "why": "...",
     "key_line": [{"function": "create_invoice", "why": "creates the purchase invoice of a scan"}, ...]}

and the line is asked when a commit changes one of them, or a function of the same file they run: every function
of the file a named function refers to, and so on, is read with it (a helper, a callback). Changed means that its
code differs once comments and docstrings are set aside; a function that only moved has not changed. A Python file
is read without being run, before and after the commit. What this cannot see is never refused: a new function
nobody named and no named function runs, a call that goes through another file, a name built while running. A
function the registry names and the file no longer holds is refused, as is a name without its reason: the list
would guard nothing without anybody noticing.

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
import ast
import fnmatch
import functools
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


def matching(config: dict, name: str) -> list[dict]:
    return [entry for entry in config.get("watched") or [] if fnmatch.fnmatchcase(name, str(entry.get("path") or ""))]


def line_scope(config: dict, name: str) -> bool | list[str]:
    """What of a file asks for the key line: False (nothing), True (any change of its code), or named functions.

    An entry that says `"key_line": false` exempts the file whatever the others say; an entry that names functions
    limits the line to them, unless another entry asks for the whole file. A test asks for nothing."""
    entries = matching(config, name)
    if not entries or is_test(name) or any(entry.get("key_line") is False for entry in entries):
        return False
    if not all(isinstance(entry.get("key_line"), list) for entry in entries) or Path(name).suffix != ".py":
        return True
    return [str(item.get("function")) for entry in entries for item in entry["key_line"] if isinstance(item, dict)]


def comments_only(root: Path, name: str, revision: str | None) -> bool:
    """Whether what a commit (or, before a commit, the working tree against HEAD) changes in a file is comments only.

    Every line added or removed is blank or a line comment. A change with no line to read (a new file not yet
    followed, a renaming, a change of mode) is not taken for comments."""
    marks = {".py": "#", ".js": "//"}.get(Path(name).suffix)
    if not marks:
        return False
    diff = (
        git(root, "show", "--format=", "-U0", revision, "--", name)
        if revision
        else git(root, "diff", "-U0", "HEAD", "--", name)
    )
    changed = [
        line[1:].strip()
        for line in diff.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
    ]
    return bool(changed) and all(not line or line.startswith(marks) for line in changed)


@functools.lru_cache(maxsize=8)
def functions_of(source: str) -> dict[str, tuple[str, set[str]]] | None:
    """{function: (its code without comments, docstrings or positions, the names it refers to)} of a Python source.

    The functions of the module and the methods of its classes (`Class.method`). None when the source does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = body[1:] or [ast.Pass()]
    found = {}
    for node in tree.body:
        owned = [(node.name, node)] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        if isinstance(node, ast.ClassDef):
            owned = [
                (f"{node.name}.{child.name}", child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        for name, function in owned:
            found[name] = (ast.dump(function), {n.id for n in ast.walk(function) if isinstance(n, ast.Name)})
    return found


def file_at(root: Path, name: str, revision: str | None) -> str:
    """A file as a revision holds it; with no revision, as the working tree holds it. '' when it is not there."""
    if revision:
        return git(root, "show", f"{revision}:{name}")
    try:
        return (root / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def functions_changed(root: Path, name: str, revision: str | None, named: list[str]) -> list[str]:
    """The named functions, and the functions of the file they run, that a commit changes in a Python file.

    Before a commit (no revision), the working tree against HEAD. A file that no longer parses is taken as changed."""
    before = functions_of(file_at(root, name, f"{revision}^" if revision else "HEAD")) or {}
    after = functions_of(file_at(root, name, revision))
    if after is None:
        return ["the file does not parse"]
    reached, todo = set(), list(named)
    while todo:
        function = todo.pop()
        if function in reached:
            continue
        reached.add(function)
        for version in (before, after):
            todo += [other for other in version.get(function, ("", set()))[1] if other in version]
    return sorted(f for f in reached if before.get(f, ("",))[0] != after.get(f, ("",))[0])


def changes_code(config: dict, root: Path, names: list[str], revision: str | None) -> list[str]:
    """What of a commit makes it need the key line: a file, or a file and its functions that changed."""
    found = []
    for name in names:
        scope = line_scope(config, Path(name).as_posix()) if name else False
        if scope is True and not comments_only(root, name, revision):
            found.append(name)
        elif isinstance(scope, list):
            changed = functions_changed(root, name, revision, scope)
            if changed:
                found.append(f"{name} ({', '.join(changed[:4])}{'…' if len(changed) > 4 else ''})")
    return found


def registry_flaws(config: dict, root: Path, revision: str | None, only: list[str] | None = None) -> list[str]:
    """What is wrong with the functions the registry names: no reason, or a function its file no longer holds.

    `only`: before a commit, the files of that commit; the file of another author is not read from the working tree."""
    flaws = []
    for entry in config.get("watched") or []:
        named = entry.get("key_line")
        if not isinstance(named, list):
            continue
        path = str(entry.get("path") or "")
        if any(mark in path for mark in "*?["):
            flaws.append(f"{CONFIG}: {path}: `key_line` names functions, so the entry names one file, not a pattern")
            continue
        for item in named:
            if not isinstance(item, dict) or not str(item.get("function") or "").strip():
                flaws.append(f"{CONFIG}: {path}: an entry of `key_line` names no `function`")
            elif not str(item.get("why") or "").strip():
                flaws.append(f"{CONFIG}: {path}: the function {item['function']} is named without its reason (`why`)")
        if only is not None and path not in only and CONFIG not in only:
            continue
        held = functions_of(file_at(root, path, revision))
        for item in named if held is not None else []:
            function = str(item.get("function") or "") if isinstance(item, dict) else ""
            if function and function not in held:
                flaws.append(
                    f"{CONFIG}: {path} no longer holds the function {function} that `key_line` names: name the "
                    "function that took its place, or the list guards nothing"
                )
    return flaws


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


def commits(root: Path, selection: list[str]) -> list[tuple[str, str, str, str, list[str]]]:
    """(sha, day, subject, message, files) of every commit of the selection, the oldest first, merges left out.

    One reading of the history for all of them: a repository takes hundreds of commits in two weeks."""
    told = git(
        root, "log", "--no-merges", "--reverse", "--name-only", "--format=%x01%H%x00%cs%x00%s%x00%B%x00", *selection
    )
    found = []
    for record in told.split("\x01")[1:]:
        sha, day, subject, message, names = record.split("\x00", 4)
        found.append((sha, day, subject, message, [name for name in names.split("\n") if name]))
    return found


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
        flaws = registry_flaws(config, root, None, [Path(name).as_posix() for name in names if name])
        for line in flaws:
            print(line)
        if flaws:
            return 1
        touched = changes_code(config, root, names, None)
        if not touched:
            return 0
        why = verdict(root, Path(args.message).read_text(encoding="utf-8"), None)
        if why:
            print(f"this commit touches a path where the assistant decides ({touched[0]}): {why}.")
            print("Add `Key: <site key>` or `Key: none, <why nothing changes for a customer>, reviewed by <who>`.")
        return 1 if why else 0

    flaws = registry_flaws(config, root, "HEAD")
    for line in flaws:
        print(line)
    if flaws:
        return 1
    selection = [f"{args.base}..HEAD"] if args.base else [f"--since={args.days} days ago", "HEAD"]
    judged = commits(root, selection)
    repairs = []
    for _sha, _day, _subject, message, _names in judged:
        for found in REPAIR_LINE.finditer(message):
            if not flaw(root, found.group("value"), "HEAD"):
                repairs.append(found.group("sha"))
    refused = warned = 0
    for sha, made, subject, message, names in judged:
        if made < since or any(sha.startswith(repair) for repair in repairs):
            continue
        touched = changes_code(config, root, names, sha)
        why = verdict(root, message, "HEAD") if touched else None
        if why:
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
