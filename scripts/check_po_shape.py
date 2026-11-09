#!/usr/bin/env python3
"""Refuse a PO file that says something else than the screen shows: an entry written twice, a msgstr made of
two translations glued end to end (neoffice-maintenance#1953).

What happened (28.10, in one app): the nightly robot re-saved the whole catalogue with polib, re-wrapping every
long msgstr, and merges with branches that had edited the same entries in the old form then left 133 entries
written twice and 56 msgstr holding two translations glued (`msgstr "A."` followed by a continuation line
`"B."`). Frappe serves the FIRST copy of an entry, so a fix made in another copy never shows, and both glued
translations are shown on screen. The robot writes in place now (translate_po.py), and this check stands behind
it: the translate-po workflow runs it on each catalogue it is about to commit.

  check_po_shape.py [--before COMMITTED.po] FILE.po [FILE.po ...]

Without --before every problem fails (exit 1). With --before, the catalogue as committed, only a problem that
the committed file did not have fails: damage that was already there is named as a warning and does not stop the
night, which would otherwise stay red, and translate nothing, until somebody had repaired it (an issue per app
tracks that). A glued msgstr is a first line that holds text and goes on over the next lines: Babel and polib
write a wrapped msgstr from `msgstr ""`, and a first line that ends with its own `\\n` is a text of several
lines, not a glue.

Same rules as `shape_problems` of the neoffice-activity app's check_glossary_words.py (one source for the idea,
copied here because the workflow runs where that app is not checked out).
"""
from __future__ import annotations

import argparse
import os
import sys


def _unquote(line: str) -> str:
    body = line[line.index('"') + 1 : line.rindex('"')]
    return body.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def read_entries(text: str) -> list[tuple[int, str, str, str]]:
    """[(line of the msgid, msgctxt, msgid, msgstr)] of a .po text: enough of the format for this check."""
    entries: list[dict] = []
    current: dict | None = None
    field: str | None = None
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if line.startswith("#") or not line:
            if current and current.get("msgstr") is not None:
                entries.append(current)
                current, field = None, None
            continue
        if line.startswith("msgctxt "):
            if current and current.get("msgstr") is not None:
                entries.append(current)
            current, field = {"ctx": _unquote(line), "line": number}, "ctx"
        elif line.startswith("msgid "):
            if current is None or current.get("msgstr") is not None:
                if current and current.get("msgstr") is not None:
                    entries.append(current)
                current = {"ctx": ""}
            current.update({"id": _unquote(line), "line": number})
            field = "id"
        elif line.startswith("msgstr"):
            if current is not None:
                current["msgstr"] = _unquote(line)
                field = "msgstr"
        elif line.startswith('"') and current is not None and field:
            current[field] = current.get(field, "") + _unquote(line)
    if current and current.get("msgstr") is not None:
        entries.append(current)
    return [(e["line"], e.get("ctx", ""), e.get("id", ""), e["msgstr"]) for e in entries if e.get("id")]


def shape_problems(text: str) -> list[tuple[int, str, str, str, str]]:
    """[(line, kind, msgctxt, msgid, what)] of what makes the file say something else than the screen shows.
    `kind` is "twice" or "glued": with the context and the msgid it names a problem, whatever line it is on."""
    out: list[tuple[int, str, str, str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    for line, ctx, msgid, _msgstr in read_entries(text):
        if (ctx, msgid) in seen:
            out.append((line, "twice", ctx, msgid, f"written twice (first copy at line {seen[(ctx, msgid)]})"))
        else:
            seen[(ctx, msgid)] = line
    lines = text.split("\n")
    ctx, msgid = "", ""
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            ctx = ""  # an entry ends at a blank line, and so does its context
        elif raw.startswith("msgctxt "):
            ctx = _unquote(raw)
        elif raw.startswith("msgid "):
            msgid = _unquote(raw)
            for following in lines[number:]:
                if not following.startswith('"'):
                    break
                msgid += _unquote(following)
        elif (
            raw.startswith("msgstr")
            and _unquote(raw)
            # a text of several lines, written line by line (frappe's own PO does), is no glue
            and not raw.endswith('\\n"')
            and lines[number : number + 1]
            and lines[number].startswith('"')
        ):
            out.append((number, "glued", ctx, msgid, "two translations glued in one msgstr: keep one"))
    return sorted(out)


def _key(problem: tuple[int, str, str, str, str]) -> tuple[str, str, str]:
    return problem[1], problem[2], problem[3]


def _read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("po", nargs="+", help="the catalogue(s) to check")
    parser.add_argument("--before", help="the catalogue as committed: only a problem it did not have fails")
    args = parser.parse_args(argv)

    known: set[tuple[str, str, str]] = set()
    if args.before and os.path.exists(args.before):
        known = {_key(p) for p in shape_problems(_read(args.before))}

    failed = 0
    for path in args.po:
        for problem in shape_problems(_read(path)):
            line, _kind, ctx, msgid, what = problem
            where = f"{path}:{line}: {msgid[:80]!r}" + (f" (context {ctx!r})" if ctx else "") + f" {what}"
            if _key(problem) in known:
                print(f"::warning::{where} (already so in the committed file)")
            else:
                print(f"::error::{where}")
                failed += 1
    if failed:
        print(
            f"{failed} new problem(s): the catalogue is not committed. An entry written twice: keep the copy the "
            "screen shows, remove the others. Two translations glued: keep one."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
