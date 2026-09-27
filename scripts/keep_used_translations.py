#!/usr/bin/env python3
"""Put back the translations `bench update-po-files` dropped while the code still uses them.

`bench update-po-files` rebuilds a catalogue from what Frappe's extractor finds, and
removes every other entry. The extractor misses whole families of strings that the code
does translate at runtime:

- a `__()` inside a JavaScript template, in an HTML attribute (`placeholder="${__("…")}"`)
  or inside a nested template;
- a label translated through a variable (`_(label)` over a dict of labels);
- a custom field's description (a fixture), translated by the form when it displays it.

Each night the Translate workflow therefore deleted human translations that were still on
screen, and the screens went back to English (neoffice-maintenance#866: 16 lost in one run,
most of them on the access screen).

This step compares the catalogue as committed (before) with the rebuilt one (after). A
translated entry that disappeared, or became obsolete, is put back when its source text
still appears in the app's code. What is really gone from the code stays gone. It prints
what it kept, so the extractor's blind spots can be fixed at the source too.

Usage: keep_used_translations.py --before OLD.po --after NEW.po --source APP_DIR
Exit 0 always; one summary line, then one line per entry kept.
"""
from __future__ import annotations

import argparse
import os
import sys

import polib

SOURCE_SUFFIXES = (".py", ".js", ".ts", ".vue", ".html", ".jinja", ".json", ".md")
SKIP_DIRS = {".git", "node_modules", "dist", "locale", "translations", "__pycache__", ".github"}
SKIP_SUFFIXES = (".po", ".pot", ".mo", ".csv", ".bak", ".min.js", ".map")
# A long msgid is often split over several source lines (implicit concatenation); its
# first characters still tell that the code uses it.
PREFIX = 40


def source_corpus(root: str) -> str:
    chunks = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(SOURCE_SUFFIXES) or name.endswith(SKIP_SUFFIXES):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as f:
                    chunks.append(f.read())
            except OSError:
                continue
    return "\n".join(chunks)


def still_used(msgid: str, corpus: str) -> bool:
    if not msgid.strip():
        return False
    variants = {msgid, msgid.replace('"', '\\"'), msgid.replace("'", "\\'"), msgid.replace("\n", "\\n")}
    if any(v in corpus for v in variants):
        return True
    return len(msgid) > PREFIX and msgid[:PREFIX] in corpus


def translated(entry: polib.POEntry) -> bool:
    if entry.obsolete or "fuzzy" in entry.flags:
        return False
    if entry.msgid_plural:
        return any(v for v in entry.msgstr_plural.values())
    return bool(entry.msgstr)


def keep(before_path: str, after_path: str, source: str) -> list[str]:
    before = polib.pofile(before_path)
    after = polib.pofile(after_path)
    active = {(e.msgctxt, e.msgid) for e in after if not e.obsolete}
    corpus = None
    kept = []
    for entry in before:
        key = (entry.msgctxt, entry.msgid)
        if key in active or not translated(entry):
            continue
        if corpus is None:
            corpus = source_corpus(source)
        if not still_used(entry.msgid, corpus):
            continue
        # An obsolete copy of it in the rebuilt catalogue gives way to the live one.
        for stale in [e for e in after if e.obsolete and (e.msgctxt, e.msgid) == key]:
            after.remove(stale)
        after.append(entry)
        active.add(key)
        kept.append(entry.msgid)
    if kept:
        after.save(after_path)
    return kept


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True, help="the catalogue as committed")
    parser.add_argument("--after", required=True, help="the catalogue update-po-files rebuilt")
    parser.add_argument("--source", required=True, help="the app's source directory")
    args = parser.parse_args(argv)
    if not os.path.exists(args.before):
        print(f"no committed catalogue at {args.before}: nothing to keep")
        return 0
    kept = keep(args.before, args.after, args.source)
    print(f"{args.after}: kept {len(kept)} translation(s) the extractor did not see but the code uses")
    for msgid in kept:
        print("  kept:", msgid[:100].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
