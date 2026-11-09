#!/usr/bin/env python3
"""Fork contracts: what we changed on purpose must still be changed after the next upstream merge.

A `//// Neoffice` marker says WHY we changed a line. It does not stop the next upstream merge from bringing the
change back: git keeps our side only where upstream left the lines alone, and when upstream moves the same rule
into another file or another form, nothing conflicts and nothing warns. On 2026-10-20 and 2026-10-30 an upstream
merge gave the Item Group field of the Item form the filter "Is Group = No" (first in the DocType JSON, then in
JavaScript); our items sit under parent groups, so the form stopped offering them and a user found out
(neoffice-maintenance#2398). The fix is a marked removal, and a marked removal protects nothing by itself.

A contract writes the intent down where a machine can read it: a table headed `| Contract |` in
NEOFFICE_FORK_MARKERS.md, one row per decision.

    | Contract | Rule | Pattern | Why |
    | --- | --- | --- | --- |
    | `erpnext/stock/doctype/item/item.js` | absent | `set_query\\(\\s*"item_group"` | #2398: parent groups stay selectable |
    | `erpnext/stock/doctype/item/item.json` | absent | `"link_filters":\\s*"\\[\\[\\\\"Item Group\\\\"` | same |

  first column  a path or a glob of the files the rule applies to, in backticks, relative to the repository root
  Rule          `absent`: no matching file may contain the pattern (upstream put back what we removed)
                `present`: every matching file must contain it, and one at least must exist (upstream took away what
                we added, or renamed the file under it)
  Pattern       a Python regular expression, in backticks, searched line by line; write `\\|` for an alternation
                (a bare `|` ends the table cell); no backtick inside
  Why           the issue and the reason, for the person whose push turns red

  fork_contracts.py [--root DIR] [--manifest FILE]
      Judge the tracked files of the working tree against the contracts. Exit 1 when one is broken, or when a row
      cannot be read (a row that never checks is worse than none). No manifest, or no table: nothing to judge, exit 0.

When it turns red: either upstream's change is unwanted (remove it again, in the same commit, with the marker), or
the decision has changed (edit the row, say why). Never delete the row to get a green run.
"""
from __future__ import annotations

import argparse
import fnmatch
import pathlib
import re
import subprocess
import sys
from typing import NamedTuple

MANIFEST = "NEOFFICE_FORK_MARKERS.md"
RULES = ("absent", "present")


class Contract(NamedTuple):
    path: str
    rule: str
    pattern: re.Pattern
    why: str
    row: int  # line number in the manifest, for the messages


def split_row(row: str) -> list[str]:
    """The cells of a markdown table row; `\\|` stays inside its cell and is written back as `|`."""
    cells = re.split(r"(?<!\\)\|", row.strip())
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [c.strip().replace("\\|", "|") for c in cells]


def unquote(cell: str) -> str:
    found = re.fullmatch(r"`(.+)`", cell)
    return found.group(1) if found else cell


def parse_contracts(manifest: list[str]) -> tuple[list[Contract], list[str]]:
    """The contracts of every table headed `| Contract |`, and the rows that could not be read."""
    contracts: list[Contract] = []
    errors: list[str] = []
    in_table = False
    for number, line in enumerate(manifest, 1):
        row = line.strip()
        if not row.startswith("|"):
            in_table = False
            continue
        cells = split_row(row)
        if not in_table:
            in_table = bool(cells) and cells[0].lower().startswith("contract")
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):  # the |---|---| line under the header
            continue
        if len(cells) < 4:
            errors.append(f"{MANIFEST}:{number}: a contract has four cells (path, rule, pattern, why)")
            continue
        path, rule, pattern, why = unquote(cells[0]), cells[1].lower(), unquote(cells[2]), cells[3]
        if rule not in RULES:
            errors.append(f"{MANIFEST}:{number}: the rule is `absent` or `present`, not `{cells[1]}`")
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            errors.append(f"{MANIFEST}:{number}: the pattern is not a regular expression ({e})")
            continue
        contracts.append(Contract(path, rule, compiled, why, number))
    return contracts, errors


def judge(contracts: list[Contract], files: dict[str, str]) -> tuple[list[str], list[str]]:
    """(broken, notes): `files` maps each tracked path to its text."""
    broken: list[str] = []
    notes: list[str] = []
    for c in contracts:
        matching = sorted(p for p in files if fnmatch.fnmatchcase(p, c.path))
        if not matching:
            if c.rule == "present":
                broken.append(f"{c.path}: no such file, so `{c.pattern.pattern}` is gone ({c.why})")
            else:
                notes.append(f"{c.path}: no such file; if upstream moved it, the contract no longer watches anything")
            continue
        for path in matching:
            lines = files[path].splitlines()
            hits = [n for n, text in enumerate(lines, 1) if c.pattern.search(text)]
            if c.rule == "absent":
                for n in hits:
                    broken.append(f"{path}:{n}: `{lines[n - 1].strip()[:110]}` is back, we removed it ({c.why})")
            elif not hits:
                broken.append(f"{path}: `{c.pattern.pattern}` is gone, we added it ({c.why})")
    return broken, notes


def tracked_files(root: pathlib.Path) -> dict[str, str]:
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True, errors="replace"
    ).stdout.split("\0")
    files: dict[str, str] = {}
    for name in filter(None, names):
        target = root / name
        if target.is_symlink() or not target.is_file():
            continue
        try:
            files[name] = target.read_text(errors="replace")
        except OSError:
            continue
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--manifest", default=MANIFEST)
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    manifest = root / args.manifest
    if not manifest.is_file():
        print(f"no {args.manifest}: no contracts to judge")
        return 0
    contracts, errors = parse_contracts(manifest.read_text(errors="replace").splitlines())
    if not contracts and not errors:
        print(f"no `| Contract |` table in {args.manifest}: no contracts to judge")
        return 0
    broken, notes = judge(contracts, tracked_files(root))
    for line in notes:
        print(f"note    {line}")
    for line in errors:
        print(f"UNREAD  {line}")
    for line in broken:
        print(f"BROKEN  {line}")
    if broken or errors:
        print(f"\n{len(broken)} contract(s) broken, {len(errors)} row(s) unread, {len(contracts)} read")
        return 1
    print(f"{len(contracts)} contract(s) kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
