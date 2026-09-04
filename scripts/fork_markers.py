#!/usr/bin/env python3
"""Fork-marker discipline for the Neoffice forks (frappe, erpnext, wiki, raven, ...).

Every change we make to code that is not ours must carry a `//// Neoffice` comment that says
why (CLAUDE.md, rule "mark every change to code that is not ours"). This script is the
mechanical half of that rule; the AI half (writing the reasons) runs in the fork-markers
workflow of bvisible/neoffice-ci.

  fork_markers.py check  --base SHA --head SHA [--json report.json] [--verbose]
      List the hunks of BASE..HEAD that add or remove non-comment lines without a `////`
      marker nearby. Exit 1 when there is at least one.
  fork_markers.py verify --base SHA [--verbose]
      Assert that the working tree differs from BASE by comments only (marker insertions):
      no removed line, every added line a comment or blank. NEOFFICE_FORK_MARKERS.md is
      free-form. Exit 1 otherwise. Run after the AI pass, before committing its work.

A hunk counts as marked when one of its added lines carries `////`, or when the new file
has a `////` line within LOOKBACK lines above the hunk (a marker placed just before the
change). A hunk made only of comment lines is a marker itself, never flagged. Files that
cannot carry comments (JSON, PO/MO, images, lockfiles, built assets) are flagged unless
NEOFFICE_FORK_MARKERS.md at HEAD names their path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

MARK = "////"
LOOKBACK = 3
MANIFEST = "NEOFFICE_FORK_MARKERS.md"

HASH_COMMENT = {".py", ".pyi", ".yml", ".yaml", ".toml", ".txt", ".cfg", ".ini", ".sh", ".bash", ".gitignore", ".dockerignore", ".conf", ".rb", ".pl", ".r"}
SLASH_COMMENT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".scss", ".css", ".less", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".swift", ".kt", ".php"}
MARKUP_COMMENT = {".html", ".htm", ".xml", ".svg", ".md", ".jinja", ".j2", ".hbs", ".mustache", ".vue"}
NOT_COMMENTABLE = {".json", ".po", ".pot", ".mo", ".csv", ".lock", ".map", ".min.js", ".min.css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".pdf", ".zip", ".gz", ".wasm", ".pyc"}
SKIP_DIRS = ("/dist/", "/node_modules/", "/__pycache__/", "/.git/", "/build/", "/public/frontend/", "/public/dist/", "/locale/", "/translations/", "/.github/")
SKIP_FILES = ("yarn.lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", MANIFEST, "manifest.json", "version.json", "sw.js", "registerSW.js")
# Built SPA output committed by the build bots (commit-the-build forks): vite hashes its chunks,
# workbox ships its runtime, and none of it is source anyone marks.
_BUILT_ASSET = re.compile(r"(/public/[^/]+/assets/|/public/[^/]+/(index|sw|workbox)[-.][^/]*\.(js|css)$|-[A-Za-z0-9_]{8}\.(js|css)(\.map)?$|/workbox-[^/]+\.js$)")


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
    if any(d in low for d in SKIP_DIRS) or os.path.basename(path) in SKIP_FILES or _BUILT_ASSET.search("/" + path):
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
    if e == "":
        return "hash"  # scripts without extension, Makefile-like files
    return "none"


_COMMENT_RE = {
    "hash": re.compile(r"^\s*(#.*)?$"),
    "slash": re.compile(r"^\s*(//.*|/\*.*|\*.*|\*/.*)?$"),
    "markup": re.compile(r"^\s*(<!--.*|-->.*|\{#.*|#\}.*|//.*|/\*.*|\*.*|\*/.*)?$"),
}


def is_comment_line(kind: str, line: str) -> bool:
    if MARK in line:
        return True
    rx = _COMMENT_RE.get(kind)
    return bool(rx and rx.match(line))


def parse_diff(diff: str):
    """Yield (path, [hunk]) from a `git diff -U0` output. hunk = dict(new_start, new_count, old_count, added, removed)."""
    files = []
    cur = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": None, "hunks": [], "binary": False}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("+++ "):
            cur["path"] = None if line[4:] == "/dev/null" else line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("Binary files"):
            cur["binary"] = True
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            cur["hunks"].append({"new_start": new_start, "new_count": new_count, "old_count": old_count, "added": [], "removed": []})
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


def marker_nearby(lines: list[str], new_start: int, new_count: int) -> bool:
    lo = max(0, new_start - 1 - LOOKBACK)
    hi = min(len(lines), new_start - 1 + max(new_count, 1) + (LOOKBACK if new_count == 0 else 0))
    return any(MARK in l for l in lines[lo:hi])


def check(repo: str, base: str, head: str, verbose: bool):
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
        if kind == "none" or f["binary"]:
            if path not in manifest_text:
                unmarked.append({"file": path, "kind": "not-commentable", "new_start": 0, "new_count": 0, "why": f"no comment syntax — needs an entry naming the path in {MANIFEST}", "snippet": []})
            continue
        lines = head_lines(head, path, repo)
        for h in f["hunks"]:
            added, removed = h["added"], h["removed"]
            code_added = [a for a in added if not is_comment_line(kind, a)]
            if not code_added and not removed:
                continue  # comment-only hunk: a marker, or documentation
            if any(MARK in a for a in added):
                continue
            if marker_nearby(lines, h["new_start"], h["new_count"]):
                continue
            if not code_added and removed and all(is_comment_line(kind, r) for r in removed):
                continue  # only comments changed
            unmarked.append({
                "file": path, "kind": "removed-only" if not added else "modified" if removed else "added",
                "new_start": h["new_start"], "new_count": h["new_count"], "old_count": h["old_count"],
                "snippet": (code_added or removed)[:3],
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
        for h in f["hunks"]:
            for r in h["removed"]:
                problems.append(f"{path}: removed line: {r[:120]}")
            for a in h["added"]:
                if kind in ("none", "skip") or not is_comment_line(kind, a):
                    problems.append(f"{path}:{h['new_start']}: added non-comment line: {a[:120]}")
                # frappe.utils.jinja.safe_render refuses a template whose SOURCE contains ".__"
                # (anti-SSTI), comments included: a marker quoting it took /raven down with a 417.
                elif path.lower().endswith((".html", ".htm", ".jinja", ".j2")) and ".__" in a:
                    problems.append(f"{path}:{h['new_start']}: '.__' in a template comment (safe_render would answer 417): {a[:120]}")
    for p in problems:
        print("NOT COMMENT-ONLY  " + p)
    print(f"verify: {'OK — comments only' if not problems else str(len(problems)) + ' problem(s)'}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--base", required=True); c.add_argument("--head", default="HEAD"); c.add_argument("--json"); c.add_argument("--repo", default="."); c.add_argument("--verbose", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("--base", required=True); v.add_argument("--repo", default="."); v.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check":
        unmarked = check(a.repo, a.base, a.head, a.verbose)
        if a.json:
            json.dump({"base": a.base, "head": a.head, "unmarked": unmarked}, open(a.json, "w"), indent=1)
        return 1 if unmarked else 0
    return 1 if verify(a.repo, a.base, a.verbose) else 0


if __name__ == "__main__":
    sys.exit(main())
