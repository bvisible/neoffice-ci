"""Refuse a change that names a client in a public repository.

A public repo is read, indexed and forked by anyone, and a name in it is a leak
that no later edit takes back out of the history. On 2026-09-10 a sweep of the 38
public bvisible repos found 34 such lines -- including payment incidents with
amounts and dates, and a test asserting on a client's company name.

Two halves, because they fail differently:

* **Structural** -- an instance id (`SRV-0123`), a client subdomain of
  `neoffice.me`. These need no list: their SHAPE identifies them, so they are
  built in and always checked.
* **By name** -- a company or a domain. Those cannot be built in: a list of
  client names inside a public repository would BE the leak. They arrive through
  the `CLIENT_NAME_PATTERNS` environment variable, one regex per line, set from a
  secret. Absent, that half simply does not run and the script says so.

The report gives **paths and line numbers only**. It never echoes the matched
text: a CI log is as public as the file, and a guard that quotes what it found
republishes it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# Ours, not a client's: naming them is how a comment stays useful.
OURS = {"osiris", "neoservice", "demo", "prod", "www", "dev", "staging", "test"}

STRUCTURAL = [
    ("an instance id", re.compile(r"\bSRV-0\d{3}\b")),
    (
        "a client subdomain",
        re.compile(r"\b(?!(?:%s)\b)[a-z0-9][a-z0-9-]{1,40}\.neoffice\.me\b" % "|".join(sorted(OURS))),
    ),
]

# Build artefacts and vendored code are not ours to police, and a minified
# bundle produces nothing a human can act on.
SKIP = re.compile(
    r"(^|/)(node_modules|dist|\.git|__pycache__|vendor|third_party)/|"
    r"public/frontend/assets/|\.(min\.js|min\.css|lock|png|jpg|jpeg|gif|svg|woff2?|ttf|ico|pdf)$"
)

TEXT = re.compile(r"\.(py|js|ts|jsx|tsx|vue|html|json|md|po|csv|txt|yml|yaml|scss|css|sh)$")


def named_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get("CLIENT_NAME_PATTERNS", "")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(re.compile(line, re.I))
        except re.error as exc:
            print(f"::warning::CLIENT_NAME_PATTERNS: {exc} -- entry ignored", file=sys.stderr)
    return out


def files_to_check(argv: list[str]) -> list[str]:
    if argv:
        return argv
    base = os.environ.get("DIFF_BASE", "")
    if base:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"],
            capture_output=True,
            text=True,
        ).stdout
        return out.split()
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
    ).stdout
    return out.split()


def main() -> int:
    named = named_patterns()
    checks = STRUCTURAL + [("a client name", p) for p in named]

    hits: list[tuple[str, int, str]] = []
    for path in files_to_check(sys.argv[1:]):
        if SKIP.search(path) or not TEXT.search(path):
            continue
        try:
            with open(path, encoding="utf8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    for label, pattern in checks:
                        if pattern.search(line):
                            hits.append((path, lineno, label))
                            break
        except FileNotFoundError:
            continue

    if not named:
        print("CLIENT_NAME_PATTERNS is not set: only the structural checks ran "
              "(instance ids, client subdomains). Company names were NOT checked.")

    if not hits:
        print(f"no client identity in {len(files_to_check(sys.argv[1:]))} changed file(s)")
        return 0

    print("A public repository must not name a client. Found:\n")
    for path, lineno, label in hits:
        print(f"  {path}:{lineno}  -- {label}")
    print(
        "\nThe matched text is deliberately not printed: a CI log is as public as "
        "the file.\n"
        "Describe the case, not the client: 'a till', 'one instance of the fleet', "
        "'a shop migrated from WordPress'. Keep every measurement, date and commit "
        "hash -- only the identity goes. The client context belongs in Obsidian and "
        "in the private tracker."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
