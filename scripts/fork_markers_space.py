#!/usr/bin/env python3
"""Rewrite hash-comment fork markers from `#////` to `# ////` (ruff/flake8 E265 want `# `).

Comments only: Python files are edited through the tokenizer (COMMENT tokens whose text
starts with `#////`), every other hash-comment file (YAML, TOML, INI, .gitignore, patches.txt,
shell, Makefile) only on lines whose first non-blank character opens the marker. Strings that
mention `#////` are never touched. The checker (`fork_markers.py`) matches the `////` substring,
so both spellings stay valid; this is a convention change, not a semantic one.

Usage: fork_markers_space.py [--dry-run] [--root DIR]   (rewrites git-tracked files under DIR)
"""
import argparse
import io
import os
import re
import subprocess
import sys
import tokenize

HASH_EXT = {".py", ".pyi", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".txt", ".sh", ".bash", ".env"}
HASH_NAMES = {".gitignore", ".gitattributes", "Makefile", "patches.txt", ".dockerignore", ".editorconfig"}
LINE_RE = re.compile(r"^(\s*)#////")


def tracked_files(root):
    out = subprocess.check_output(["git", "-C", root, "ls-files", "-z"]).decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


def rewrite_python(text):
    """Return (new_text, n) rewriting COMMENT tokens that start with '#////'."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return rewrite_lines(text)  # unparsable (py2 file, template): line-start only
    lines = text.split("\n")
    edits = []
    for tok in tokens:
        if tok.type == tokenize.COMMENT and tok.string.startswith("#////"):
            edits.append((tok.start[0] - 1, tok.start[1]))
    for lineno, col in sorted(edits, reverse=True):
        line = lines[lineno]
        assert line[col:col + 5] == "#////", (lineno, line)
        lines[lineno] = line[:col] + "# ////" + line[col + 5:]
    return "\n".join(lines), len(edits)


def rewrite_lines(text):
    n = 0
    out = []
    for line in text.split("\n"):
        m = LINE_RE.match(line)
        if m:
            line = m.group(1) + "# ////" + line[m.end():]
            n += 1
        out.append(line)
    return "\n".join(out), n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    total_files = total_edits = 0
    for rel in tracked_files(root):
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1].lower()
        if not (ext in HASH_EXT or base in HASH_NAMES):
            continue
        if "/public/" in "/" + rel or "/node_modules/" in "/" + rel or "/dist/" in "/" + rel:
            continue
        path = os.path.join(root, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        if b"#////" not in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            print(f"SKIP (not utf-8): {rel}", file=sys.stderr)
            continue
        new, n = rewrite_python(text) if ext in (".py", ".pyi") else rewrite_lines(text)
        if n and new != text:
            total_files += 1
            total_edits += n
            print(f"{n:5d}  {rel}")
            if not args.dry_run:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
    print(f"{'would rewrite' if args.dry_run else 'rewrote'} {total_edits} marker(s) in {total_files} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
