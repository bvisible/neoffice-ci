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

The entries put back are written into the rebuilt file as text: each one exactly as the committed
catalogue had it, appended, and an obsolete copy of it removed by its lines. polib only reads. Saving
with polib re-wrapped every long msgstr of the file at 78 columns, and merges of that rewrite left
entries written twice and msgstrs glued together (neoffice-maintenance#1953). The file is written only
when a re-read proves that nothing else moved.

Usage: keep_used_translations.py --before OLD.po --after NEW.po --source APP_DIR
Exit 0, one summary line, then one line per entry kept; exit 1 when the write was refused.
"""
from __future__ import annotations

import argparse
import os
import re
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


_FIELD = re.compile(r'^(#~ )?(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?) "(.*)"$')


class WriteRefused(RuntimeError):
    """The rebuilt file was not written: the re-read could not prove that only the kept entries were added."""


def _blocks(text: str) -> list[list[str]]:
    """The entries of a PO text as runs of non-blank lines, in file order (an entry ends at a blank line)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _key(block: list[str]) -> tuple[bool, str | None, str | None]:
    """(obsolete, msgctxt, msgid) of one entry's lines; msgid is None for a block that holds no entry."""
    fields: dict[str, str] = {}
    field = None
    obsolete = False
    for raw in block:
        line = raw.rstrip("\r\n")
        found = _FIELD.match(line)
        if found:
            prefix, field, body = found.groups()
            obsolete = obsolete or bool(prefix)
            fields[field] = polib.unescape(body)
            continue
        cont = line[3:] if line.startswith("#~ ") else line
        if field and len(cont) >= 2 and cont[0] == '"' and cont[-1] == '"':
            fields[field] += polib.unescape(cont[1:-1])
        else:
            field = None
    return obsolete, fields.get("msgctxt"), fields.get("msgid")


def _state(text: str) -> tuple[dict, dict]:
    """({key: msgstr} of the live entries, {key: msgstr} of the obsolete ones), as polib reads `text`."""
    live, gone = {}, {}
    for entry in polib.pofile(text):
        target = gone if entry.obsolete else live
        target[(entry.msgctxt, entry.msgid)] = (entry.msgstr, tuple(sorted(entry.msgstr_plural.items())))
    return live, gone


def _write_kept(after_path: str, before_text: str, keys: list[tuple[str | None, str]]) -> None:
    """Append the committed lines of each kept entry to the rebuilt file and drop its obsolete copies there.

    Every other line of the rebuilt file stays as it is; refused (WriteRefused) when the re-read differs from
    the rebuilt catalogue plus the kept entries in anything else."""
    with open(after_path, encoding="utf-8", newline="") as f:
        after_text = f.read()
    wanted = set(keys)
    committed = {}
    for block in _blocks(before_text):
        obsolete, msgctxt, msgid = _key(block)
        if not obsolete and msgid is not None and (msgctxt, msgid) in wanted:
            committed.setdefault((msgctxt, msgid), block)
    missing = wanted - committed.keys()
    if missing:
        raise WriteRefused(f"no committed lines found for {len(missing)} kept entr(y/ies)")

    # Remove each obsolete copy of a kept entry by its own lines; nothing else of the file moves.
    out: list[str] = []
    removed = set()
    pending: list[str] = []
    lines = after_text.splitlines(keepends=True)

    def flush():
        obsolete, msgctxt, msgid = _key(pending)
        if obsolete and (msgctxt, msgid) in wanted:
            removed.add((msgctxt, msgid))
            # the blank line that separated it from the previous entry goes with it
            if out and not out[-1].strip():
                out.pop()
        else:
            out.extend(pending)

    for line in lines:
        if line.strip():
            pending.append(line)
            continue
        if pending:
            flush()
            pending = []
        out.append(line)
    if pending:
        flush()

    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    for key in keys:
        text += "\n" + "".join(committed[key])
        if not text.endswith("\n"):
            text += "\n"

    live_before, gone_before = _state(after_text)
    live_after, gone_after = _state(text)
    committed_live = _state("".join("".join(committed[k]) + "\n" for k in keys))[0]
    expected_live = {**live_before, **{k: committed_live[k] for k in keys}}
    expected_gone = {k: v for k, v in gone_before.items() if k not in removed}
    if live_after != expected_live or gone_after != expected_gone:
        raise WriteRefused("the re-read differs from the rebuilt catalogue plus the kept entries")
    with open(after_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def keep(before_path: str, after_path: str, source: str) -> list[str]:
    with open(before_path, encoding="utf-8", newline="") as f:
        before_text = f.read()
    before = polib.pofile(before_text)
    after = polib.pofile(after_path)
    active = {(e.msgctxt, e.msgid) for e in after if not e.obsolete}
    corpus = None
    kept = []
    keys = []
    for entry in before:
        key = (entry.msgctxt, entry.msgid)
        if key in active or not translated(entry):
            continue
        if corpus is None:
            corpus = source_corpus(source)
        if not still_used(entry.msgid, corpus):
            continue
        active.add(key)
        keys.append(key)
        kept.append(entry.msgid)
    if keys:
        _write_kept(after_path, before_text, keys)
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
    try:
        kept = keep(args.before, args.after, args.source)
    except WriteRefused as refused:
        print(f"{args.after}: nothing written, {refused}", file=sys.stderr)
        return 1
    print(f"{args.after}: kept {len(kept)} translation(s) the extractor did not see but the code uses")
    for msgid in kept:
        print("  kept:", msgid[:100].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
