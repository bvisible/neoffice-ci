#!/usr/bin/env python3
"""Fork-marker discipline for the Neoffice forks (frappe, erpnext, wiki, raven, ...).

Every change we make to code that is not ours must carry a `//// Neoffice` comment that says
why (CLAUDE.md, rule "mark every change to code that is not ours"). This script is the
mechanical half of that rule; the AI half (writing the reasons) runs in the fork-markers
workflow of bvisible/neoffice-ci.

  fork_markers.py check  --base SHA --head SHA [--upstream-base SHA] [--json report.json] [--verbose]
      List the hunks of BASE..HEAD that add or remove non-comment lines without a `////`
      marker nearby. Exit 1 when there is at least one.
  fork_markers.py verify --base SHA [--verbose]
      Assert that the working tree differs from BASE by comments only (marker insertions):
      no removed line, every added line a comment or blank. NEOFFICE_FORK_MARKERS.md is
      free-form. Exit 1 otherwise. Run after the AI pass, before committing its work.

A hunk counts as marked when one of its added lines carries `////`, or when the new file
has a `////` line within LOOKBACK lines above the hunk (a marker placed just before the
change). A hunk made only of comment lines is a marker itself, never flagged — and in a
Python file a docstring counts as a comment: it cannot carry a `#` marker, and the lines
above it are more docstring, so no placement could ever satisfy the check
(neoffice-maintenance#293). Files that cannot carry comments (JSON, PO/MO, images,
lockfiles, built assets) are flagged unless NEOFFICE_FORK_MARKERS.md at HEAD names their
path.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tokenize

MARK = "////"
LOOKBACK = 3
MANIFEST = "NEOFFICE_FORK_MARKERS.md"

HASH_COMMENT = {".py", ".pyi", ".yml", ".yaml", ".toml", ".txt", ".cfg", ".ini", ".sh", ".bash", ".gitignore", ".dockerignore", ".conf", ".rb", ".pl", ".r"}
SLASH_COMMENT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".scss", ".css", ".less", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".swift", ".kt", ".php"}
MARKUP_COMMENT = {".html", ".htm", ".xml", ".svg", ".md", ".jinja", ".j2", ".hbs", ".mustache", ".vue"}
# SQL comments with `--` (and `#` on MariaDB, and /* */): the schema files of a fork carry markers
# like any other source. Classing them "not commentable" sent every marked line of
# framework_mariadb.sql to the manifest and kept the run red for nothing.
SQL_COMMENT = {".sql"}
NOT_COMMENTABLE = {".json", ".po", ".pot", ".mo", ".csv", ".lock", ".map", ".min.js", ".min.css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".pdf", ".zip", ".gz", ".wasm", ".pyc"}
SKIP_DIRS = ("/dist/", "/node_modules/", "/__pycache__/", "/.git/", "/build/", "/public/frontend/", "/public/dist/", "/locale/", "/translations/", "/.github/")
SKIP_FILES = ("yarn.lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", MANIFEST, "manifest.json", "version.json", "sw.js", "service-worker.js", "registerSW.js")
# Built SPA output committed by the build bots (commit-the-build forks): vite hashes its chunks,
# workbox ships its runtime, and none of it is source anyone marks.
_BUILT_ASSET = re.compile(r"(/public/[^/]+/assets/|/public/[^/]+/(index|sw|workbox)[-.][^/]*\.(js|css)$|-[A-Za-z0-9_]{8}\.(js|css)(\.map)?$|/workbox-[^/]+\.js$)")
# A vendored bundle is a build output someone committed, not source anyone edits:
# `public/js/lib/` is where frappe keeps third-party libraries, and a `*.global.js`
# is a tsup IIFE build (that is how the NeoCockpit React library reaches the desk).
# They are minified onto a handful of enormous lines, so there is no line to write a
# marker on — and the check stayed red on every push for five hunks nobody could
# ever mark. A verifier that cannot be satisfied is a verifier people stop reading.
_VENDORED_BUNDLE = re.compile(r"(/public/js/lib/|\.global\.js$)")


def sh(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, errors="replace").stdout


def ext_of(path: str) -> str:
    low = path.lower()
    for multi in (".min.js", ".min.css", ".bundle.js", ".bundle.css"):
        if low.endswith(multi):
            return multi
    return os.path.splitext(low)[1]


def kind_of(path: str) -> str:
    """'hash' | 'slash' | 'markup' | 'none' | 'skip'"""
    low = "/" + path.lower()
    if (
        any(d in low for d in SKIP_DIRS)
        or os.path.basename(path) in SKIP_FILES
        or _BUILT_ASSET.search("/" + path)
        or _VENDORED_BUNDLE.search("/" + path)
    ):
        return "skip"
    e = ext_of(path)
    if e in NOT_COMMENTABLE or e in (".bundle.js", ".bundle.css"):
        return "none"
    if e in HASH_COMMENT:
        return "hash"
    if e in SLASH_COMMENT:
        return "slash"
    if e in MARKUP_COMMENT:
        return "markup"
    if e in SQL_COMMENT:
        return "sql"
    if e == "":
        return "hash"  # scripts without extension, Makefile-like files
    return "none"


_COMMENT_RE = {
    "hash": re.compile(r"^\s*(#.*)?$"),
    "slash": re.compile(r"^\s*(//.*|/\*.*|\*.*|\*/.*)?$"),
    "markup": re.compile(r"^\s*(<!--.*|-->.*|\{#.*|#\}.*|//.*|/\*.*|\*.*|\*/.*)?$"),
    "sql": re.compile(r"^\s*(--.*|#.*|/\*.*|\*.*|\*/.*)?$"),
}


def is_comment_line(kind: str, line: str) -> bool:
    if MARK in line:
        return True
    rx = _COMMENT_RE.get(kind)
    return bool(rx and rx.match(line))


_BLOCK_DELIMS = {
    "slash": (("/*", "*/"),),
    "markup": (("<!--", "-->"), ("{#", "#}"), ("/*", "*/")),
    "sql": (("/*", "*/"),),
}


_HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")


def template_literal_lines(lines: list[str]) -> set[int]:
    """1-based numbers of the lines that sit INSIDE a JS template literal.

    There, `////` is not a comment: it is content, and it is rendered on screen. The
    bot wrote two such lines into frappe's form hero, and every document with a key
    value displayed the sentence under its amount (neoffice-maintenance#331). Nothing
    catches it otherwise -- the file still parses, the tests still pass, and the diff
    looks exactly like a comment.

    A small scanner rather than a regex, because the states NEST and alternate: a
    `${...}` inside a literal is code again (so a `//` in there IS a comment), that
    code may open another literal (`${x ? `a` : `b`}` is everywhere), and a backtick
    inside a quoted string or a comment opens nothing at all. The stack is what says
    which of the two we are in, which a depth counter cannot.
    """
    inside: set[int] = set()
    stack: list[tuple[str, int]] = []   # ("lit", 0) | ("sub", brace depth on entry)
    quote = ""                          # ' or " while inside an ordinary string
    block = False                       # inside /* */
    braces = 0

    def in_literal() -> bool:
        return bool(stack) and stack[-1][0] == "lit"

    for n, line in enumerate(lines, 1):
        if in_literal() and not quote and not block:
            inside.add(n)
        i, L = 0, len(line)
        while i < L:
            c = line[i]
            two = line[i : i + 2]
            if block:
                if two == "*/":
                    block = False
                    i += 2
                    continue
                i += 1
                continue
            if quote:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = ""
                i += 1
                continue
            if in_literal():
                if c == "\\":
                    i += 2
                    continue
                if two == "${":
                    stack.append(("sub", braces))
                    braces += 1
                    i += 2
                    continue
                if c == "`":
                    stack.pop()
                i += 1
                continue
            # plain code -- either the top level or inside a ${ }
            if two == "//":
                break                    # the rest of the line is a comment
            if two == "/*":
                block = True
                i += 2
                continue
            if c in "'\"":
                quote = c
                i += 1
                continue
            if c == "`":
                stack.append(("lit", 0))
                i += 1
                continue
            if c == "{":
                braces += 1
            elif c == "}":
                braces -= 1
                if stack and stack[-1][0] == "sub" and braces == stack[-1][1]:
                    stack.pop()          # back inside the literal that opened it
            i += 1
    return inside


def block_comment_lines(lines: list[str], kind: str) -> set[int]:
    """1-based numbers of the lines that sit INSIDE a block comment.

    A `/* … */` (or `<!-- … -->`, `{# … #}`) marker spans several lines, and its middle lines
    start with ordinary words. The line regex only recognises a line that OPENS or continues with
    a delimiter, so the marker pass wrote a perfectly good multi-line comment and its own verifier
    then called it code — every push to builder went red and no marker was ever written
    (neoffice-maintenance#205, 2026-09-09). Membership in a block is what makes a line a comment,
    not how it happens to start.
    """
    covered: set[int] = set()
    delims = _BLOCK_DELIMS.get(kind)
    if not delims:
        return covered
    open_tok = close_tok = None
    for n, line in enumerate(lines, start=1):
        rest = line
        while rest:
            if open_tok is None:
                nxt = min(
                    ((rest.find(o), o, c) for o, c in delims if rest.find(o) != -1),
                    default=None,
                )
                if nxt is None:
                    break
                i, open_tok, close_tok = nxt
                rest = rest[i + len(open_tok):]
                covered.add(n)
            else:
                covered.add(n)
                j = rest.find(close_tok)
                if j == -1:
                    break
                rest = rest[j + len(close_tok):]
                open_tok = close_tok = None
    return covered


def parse_diff(diff: str):
    """Yield (path, [hunk]) from a `git diff -U0` output. hunk = dict(old_start, old_count, new_start, new_count, added, removed)."""
    files = []
    cur = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": None, "old_path": None, "hunks": [], "binary": False}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("--- "):
            cur["old_path"] = None if line[4:] == "/dev/null" else line[6:] if line.startswith("--- a/") else line[4:]
        elif line.startswith("+++ "):
            cur["path"] = None if line[4:] == "/dev/null" else line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("Binary files"):
            cur["binary"] = True
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            cur["hunks"].append({"old_start": old_start, "old_count": old_count, "new_start": new_start, "new_count": new_count, "added": [], "removed": []})
        elif cur["hunks"]:
            h = cur["hunks"][-1]
            if line.startswith("+"):
                h["added"].append(line[1:])
            elif line.startswith("-"):
                h["removed"].append(line[1:])
    return [f for f in files if f["path"] is not None or f["binary"]]


def head_lines(head: str, path: str, repo: str) -> list[str]:
    try:
        return sh("git", "show", f"{head}:{path}", cwd=repo).splitlines()
    except subprocess.CalledProcessError:
        return []


def docstring_lines(lines: list[str]) -> set[int]:
    """1-based numbers of the lines covered by bare string statements — docstrings — in a Python file.

    A docstring cannot carry a `#` marker and the LOOKBACK lines above it are more docstring, so a
    docstring edit in a fork file could never pass the check, and the marker pass could never make
    it pass (neoffice-maintenance#293). Documentation is not code: those lines count as comments.
    Only bare string STATEMENTS qualify; a string assigned or passed (an SQL query, a template) is
    code and stays subject to the rule. A file tokenize cannot read yields nothing: back to strict.
    """
    covered: set[int] = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return covered
    statement_start = (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    prev = None
    for i, tok in enumerate(toks):
        if tok.type in (tokenize.NL, tokenize.COMMENT):
            continue
        if tok.type == tokenize.STRING and prev in statement_start:
            following = next((t.type for t in toks[i + 1:] if t.type not in (tokenize.NL, tokenize.COMMENT)), None)
            if following in (tokenize.NEWLINE, tokenize.ENDMARKER, None):
                covered.update(range(tok.start[0], tok.end[0] + 1))
        prev = tok.type
    return covered


_OWN_FILE_RE = re.compile(r"////.*added file", re.IGNORECASE)
OWN_FILE_HEAD = 12  # the convention puts the header at the very top


def is_own_file(lines: list[str]) -> bool:
    """True when the file declares itself as ours, with no upstream counterpart.

    The convention (CLAUDE.md) asks for a header — `//// Neoffice — added file (no upstream
    equivalent)` — on a file upstream does not ship. The `////` map exists to tell OUR intent from
    THEIRS inside a file we both have; in a file that is ours whole there is no theirs, so a marker
    per hunk says nothing and the pass rewrites the same file forever. One such hunk sat in the
    middle of a prompt STRING, where no comment can go at all, and kept a fork's run red
    (neoffice-maintenance#205, 2026-09-09).
    """
    return any(_OWN_FILE_RE.search(l) for l in lines[:OWN_FILE_HEAD])


def marker_nearby(lines: list[str], new_start: int, new_count: int) -> bool:
    lo = max(0, new_start - 1 - LOOKBACK)
    hi = min(len(lines), new_start - 1 + max(new_count, 1) + (LOOKBACK if new_count == 0 else 0))
    return any(MARK in l for l in lines[lo:hi])


def same_as_upstream(repo: str, upstream_base: str, head: str, path: str) -> bool:
    """True when `path` at `head` is byte-identical to the same path at `upstream_base`.

    Any error (unknown revision, path absent upstream) answers False, so the hunk stays checked.
    """
    r = subprocess.run(["git", "diff", "--quiet", upstream_base, head, "--", path], cwd=repo, capture_output=True)
    return r.returncode == 0


def check(repo: str, base: str, head: str, verbose: bool, upstream_base: str | None = None):
    diff = sh("git", "diff", "--unified=0", "--no-color", "--no-ext-diff", base, head, "--", ".", cwd=repo)
    manifest = head_lines(head, MANIFEST, repo)
    manifest_text = "\n".join(manifest)
    unmarked = []
    for f in parse_diff(diff):
        path = f["path"]
        if path is None:  # deleted file
            continue
        kind = kind_of(path)
        if kind == "skip":
            continue
        # A file byte-identical to the upstream it is based on carries no divergence, so there is
        # nothing for a marker to explain, whatever the range changed in it. Without this, taking a
        # regenerated file back to upstream's exact bytes read as an unexplained change, and the
        # marking pass wrote a marker that CREATED the divergence it claimed to document (crm,
        # frontend/auto-imports.d.ts, 2026-09-11).
        if upstream_base and same_as_upstream(repo, upstream_base, head, path):
            continue
        if kind == "none" or f["binary"]:
            if path not in manifest_text:
                unmarked.append({"file": path, "kind": "not-commentable", "new_start": 0, "new_count": 0, "why": f"no comment syntax — needs an entry naming the path in {MANIFEST}", "snippet": []})
            continue
        lines = head_lines(head, path, repo)
        if is_own_file(lines):
            continue  # ours whole: the file header is the marker, per-hunk ones say nothing
        python = kind == "hash" and path.lower().endswith((".py", ".pyi"))
        doc_head = docstring_lines(lines) if python else set()
        doc_base = None  # the BASE file is only read when a hunk removes lines
        for h in f["hunks"]:
            added, removed = h["added"], h["removed"]
            # in a -U0 hunk the added lines sit at new_start.., the removed ones at old_start..
            code_added = [a for i, a in enumerate(added) if not is_comment_line(kind, a) and (h["new_start"] + i) not in doc_head]
            if removed and doc_base is None:
                doc_base = docstring_lines(head_lines(base, f["old_path"] or path, repo)) if python else set()
            code_removed = [r for j, r in enumerate(removed) if not is_comment_line(kind, r) and (h["old_start"] + j) not in (doc_base or set())]
            if not code_added and not code_removed:
                continue  # comments, blanks or docstrings only: a marker, or documentation
            if any(MARK in a for a in added):
                continue
            if marker_nearby(lines, h["new_start"], h["new_count"]):
                continue
            unmarked.append({
                "file": path, "kind": "removed-only" if not added else "modified" if removed else "added",
                "new_start": h["new_start"], "new_count": h["new_count"], "old_count": h["old_count"],
                "snippet": (code_added or code_removed)[:3],
                "why": "no `////` marker in the hunk nor within %d lines above it" % LOOKBACK,
            })
    if verbose or True:
        for u in unmarked:
            loc = f"{u['file']}:{u['new_start']}" if u["new_start"] else u["file"]
            print(f"UNMARKED {u['kind']:<15} {loc}  ({u['why']})")
            for s in u["snippet"]:
                print("    | " + s[:140])
    print(f"{len(unmarked)} unmarked hunk(s) in {base[:10]}..{head[:10]}")
    return unmarked


def verify(repo: str, base: str, verbose: bool) -> list[str]:
    diff = sh("git", "diff", "--unified=0", "--no-color", "--no-ext-diff", base, "--", ".", f":!{MANIFEST}", cwd=repo)
    problems = []
    for f in parse_diff(diff):
        path = f["path"] or "(deleted file)"
        kind = kind_of(path)
        # the middle lines of a multi-line marker start with ordinary words: read the file as it
        # now stands and ask whether the line sits inside a block comment
        inside: set[int] = set()
        # `////` inside a JS template literal is not a comment, it is text on screen
        # (neoffice-maintenance#331). is_comment_line() waves it through because the
        # marker is in the line, so the literal has to be found separately.
        in_literal: set[int] = set()
        if kind in _BLOCK_DELIMS:
            try:
                src = pathlib.Path(repo, path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                src = []
            inside = block_comment_lines(src, kind) if src else set()
            if src and ext_of(path) in (".js", ".ts", ".jsx", ".tsx", ".vue", ".mjs", ".cjs"):
                in_literal = template_literal_lines(src)
        for h in f["hunks"]:
            for r in h["removed"]:
                problems.append(f"{path}: removed line: {r[:120]}")
            for i, a in enumerate(h["added"]):
                if kind in ("none", "skip") or (
                    not is_comment_line(kind, a) and (h["new_start"] + i) not in inside
                ):
                    problems.append(f"{path}:{h['new_start']}: added non-comment line: {a[:120]}")
                # frappe.utils.jinja.safe_render refuses a template whose SOURCE contains ".__"
                # (anti-SSTI), comments included: a marker quoting it took /raven down with a 417.
                elif path.lower().endswith((".html", ".htm", ".jinja", ".j2")) and ".__" in a:
                    problems.append(f"{path}:{h['new_start']}: '.__' in a template comment (safe_render would answer 417): {a[:120]}")
                # A marker inside a template literal is rendered to the user: it took a
                # sentence about a registry onto every document's hero (#331).
                # The exception is an HTML comment: when the literal BUILDS HTML,
                # `<!-- … -->` is a real comment in the DOM and nobody sees it. Refusing
                # that too would leave no legal way to mark those hunks at all -- and a
                # guard with no way out is a guard someone switches off.
                elif (h["new_start"] + i) in in_literal and not _HTML_COMMENT.match(a):
                    problems.append(
                        f"{path}:{h['new_start'] + i}: marker inside a template literal — it would be DISPLAYED, "
                        f"not commented. Move it above the statement that opens the literal, or use <!-- --> "
                        f"if the literal builds HTML: {a[:100]}"
                    )
    for p in problems:
        print("NOT COMMENT-ONLY  " + p)
    print(f"verify: {'OK — comments only' if not problems else str(len(problems)) + ' problem(s)'}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--base", required=True); c.add_argument("--head", default="HEAD"); c.add_argument("--json"); c.add_argument("--repo", default="."); c.add_argument("--verbose", action="store_true")
    c.add_argument("--upstream-base", help="upstream commit the fork is based on: files identical to it need no marker")
    v = sub.add_parser("verify"); v.add_argument("--base", required=True); v.add_argument("--repo", default="."); v.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check":
        unmarked = check(a.repo, a.base, a.head, a.verbose, a.upstream_base)
        if a.json:
            json.dump({"base": a.base, "head": a.head, "unmarked": unmarked}, open(a.json, "w"), indent=1)
        return 1 if unmarked else 0
    return 1 if verify(a.repo, a.base, a.verbose) else 0


if __name__ == "__main__":
    sys.exit(main())
