#!/usr/bin/env python3
"""Upstream radar: what an upstream merge brought that restricts a screen.

  upstream_radar.py --base REF --head REF [--markdown]
      List the lines BASE..HEAD adds that narrow what a user can pick or must fill in: a `link_filters` on a Link
      field, a `set_query` / `get_query` in JavaScript, a field turned mandatory. For a merge commit M, BASE is its
      first parent (`M^1`): the diff is then exactly what the other side, upstream, brought. Always exit 0: it is a
      list for a person to try on screen, not a verdict.

Why it exists (neoffice-maintenance#2398): an upstream merge added "Is Group = No" to the Item Group field of the Item
form. Nobody read that line among the hundreds of commits the merge carried, and nothing failed, because the server
enforced nothing: a user found that the form no longer offered Products and Services. A restriction on a screen is a
change of behaviour no test of ours looks at, and our data is what it collides with. This lists them, at the moment
the merge lands, so that someone tries those screens on osiris with the data we have, and runs
`scripts/link-filter-audit` of neoffice-devops, which judges the declared filters against that data.

What it does not see: a restriction written in Python (a `validate` that throws), a permission change, a field
hidden or made read-only. Those are for the whitelist check and the three-identity test.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import NamedTuple

SCRIPT_EXT = (".js", ".ts", ".tsx", ".vue", ".jsx")
PATTERNS = {
    "json": [
        ("link filter", re.compile(r'"link_filters"\s*:\s*"\[\s*\[')),
        ("mandatory", re.compile(r'"reqd"\s*:\s*1\b')),
    ],
    "script": [
        ("query filter", re.compile(r"\bset_query\s*\(")),
        ("query filter", re.compile(r"\bget_query\s*(=|:)")),
        ("link filter", re.compile(r"\blink_filters\b")),
    ],
}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class Finding(NamedTuple):
    kind: str
    path: str
    line: int
    text: str


def family(path: str) -> str | None:
    low = path.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith(SCRIPT_EXT) and "/public/dist/" not in low and "/node_modules/" not in low:
        return "script"
    return None


def scan_diff(diff: str) -> list[Finding]:
    """The restrictive lines a unified diff adds (`git diff --unified=0`)."""
    found: list[Finding] = []
    path, patterns, new_line = "", [], 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/") if raw[4:] != "/dev/null" else ""
            kind = family(path) if path else None
            patterns = PATTERNS[kind] if kind else []
            continue
        hunk = HUNK.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            text = raw[1:]
            for label, pattern in patterns:
                if pattern.search(text):
                    found.append(Finding(label, path, new_line, text.strip()))
                    break
            new_line += 1
    return found


def render(findings: list[Finding], markdown: bool) -> str:
    if not findings:
        return "nothing that narrows a screen"
    if markdown:
        rows = ["| What | Where | Line |", "| --- | --- | --- |"]
        rows += [f"| {f.kind} | `{f.path}:{f.line}` | `{f.text[:100].replace('|', '/')}` |" for f in findings]
        return "\n".join(rows)
    return "\n".join(f"{f.kind:13} {f.path}:{f.line}: {f.text[:110]}" for f in findings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    diff = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", "--no-ext-diff", args.base, args.head],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout
    print(render(scan_diff(diff), args.markdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
