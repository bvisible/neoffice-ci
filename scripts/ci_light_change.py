#!/usr/bin/env python3
"""Does a push change nothing but documentation and translations since the last green run? Then its heavy suite
need not run, and a light check stands in for it (neoffice-maintenance#2558).

Every push used to run the whole suite of its repository, whatever it touched: a sentence added to a skill of
neoffice-devops paid its twenty minutes, a hand-fixed French catalogue of the theme its fifty. A push whose files
are all light is now told apart:

  * any Markdown file (`*.md`), anything under `docs/` or `.claude/`;
  * a translation catalogue of the app, `<anything>/locale/<name>.po` or `.pot`.

The range is NOT the push's own `before..sha`. It runs from the head of the LAST GREEN run of the branch: pushes
cancel each other's runs (a newer push supersedes the run of the previous one), so a code commit whose run was
cancelled, or which went red, is still in the range of the next push, and that push runs the suite. A head that is
red stays red until a full run says otherwise.

  ci_light_change.py decide --repo OWNER/NAME --base SHA --head SHA
        compares base...head through the GitHub API (GITHUB_TOKEN from the environment) and prints
        `light=true|false`, `reason=<one line>` and `po=<JSON list of the catalogues to read>`;
        appends the same lines to $GITHUB_OUTPUT when it is set.
  ci_light_change.py last-success --repo OWNER/NAME --workflow FILE.yml --branch BRANCH
        prints the head sha of the last successful push run of that workflow on that branch (empty when none).
  ci_light_change.py classify < paths.txt
        the same rule on a list of paths, one per line (by hand, and for the tests).

It never fails a run and it never guesses: no base, a base that is not an ancestor of the head (a forced push),
a comparison longer than the API lists, an empty one, an API that does not answer — all of them say
`light=false`, and the suite runs.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
# The comparison lists at most 300 files; a list that long may be cut, so it is never trusted to be complete.
MAX_FILES = 300
ZERO = "0" * 40
_CATALOGUE = re.compile(r"(^|/)locale/[^/]+\.pot?$")


def is_light(path: str) -> bool:
    """A path whose content no build reads and no heavy test needs: documentation or a translation catalogue."""
    p = (path or "").strip()
    if not p:
        return False
    if p.lower().endswith(".md"):
        return True
    if p.startswith(("docs/", ".claude/")):
        return True
    return bool(_CATALOGUE.search(p))


def is_catalogue(path: str) -> bool:
    return bool(_CATALOGUE.search((path or "").strip()))


def classify(paths: list[str]) -> tuple[bool, str, list[str]]:
    """(light, reason, catalogues to read) for a list of changed paths."""
    paths = [p.strip() for p in paths if p and p.strip()]
    if not paths:
        return False, "no changed file to judge", []
    heavy = [p for p in paths if not is_light(p)]
    if heavy:
        more = f" and {len(heavy) - 3} more" if len(heavy) > 3 else ""
        return False, f"{len(heavy)} file(s) beyond documentation and translations ({', '.join(heavy[:3])}{more})", []
    po = sorted({p for p in paths if is_catalogue(p)})
    return True, f"only documentation and translations changed ({len(paths)} file(s))", po


def judge_comparison(payload: dict | None) -> tuple[bool, str, list[str]]:
    """The verdict on a GitHub comparison (`GET /repos/{repo}/compare/{base}...{head}`)."""
    if not isinstance(payload, dict):
        return False, "the comparison could not be read", []
    status = payload.get("status")
    if status != "ahead":
        # behind or diverged: the last green commit is not under the head (a forced push); identical: no change.
        return False, f"the last green commit is not an ancestor of the head (comparison: {status or 'none'})", []
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return False, "the comparison lists no file", []
    if len(files) >= MAX_FILES:
        return False, f"the comparison lists {len(files)} files, the API's ceiling: it may be cut", []
    paths, removed = [], set()
    for f in files:
        name = (f or {}).get("filename") or ""
        paths.append(name)
        # A rename moves a file OUT of somewhere too: `app/x.py` renamed to `docs/x.md` removed code.
        if f.get("previous_filename"):
            paths.append(f["previous_filename"])
        if f.get("status") == "removed":
            removed.add(name)
    light, reason, po = classify(paths)
    return light, reason, [p for p in po if p not in removed]


def _get(path: str, token: str, attempts: int = 2):
    req = urllib.request.Request(
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "neoffice-ci",
        },
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if attempt + 1 == attempts:
                return None
            time.sleep(5)
    return None


def _valid_sha(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", s or "")) and s != ZERO


def _valid_repo(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", s or ""))


def decide(repo: str, base: str, head: str, token: str) -> tuple[bool, str, list[str]]:
    if not _valid_repo(repo):
        return False, "no repository to compare in", []
    if not _valid_sha(base):
        return False, "no green run to compare with", []
    if not _valid_sha(head):
        return False, "no head to compare", []
    if base == head:
        return False, "the head is the last green commit itself: nothing to compare", []
    if not token:
        return False, "no token to read the comparison", []
    return judge_comparison(_get(f"/repos/{repo}/compare/{base}...{head}", token))


def last_success(repo: str, workflow: str, branch: str, token: str) -> str:
    if not (_valid_repo(repo) and re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow or "") and branch and token):
        return ""
    q = urllib.parse.urlencode({"branch": branch, "event": "push", "status": "success", "per_page": 1})
    data = _get(f"/repos/{repo}/actions/workflows/{workflow}/runs?{q}", token)
    runs = (data or {}).get("workflow_runs") or []
    sha = (runs[0] or {}).get("head_sha", "") if runs else ""
    return sha if _valid_sha(sha) else ""


def _emit(light: bool, reason: str, po: list[str]) -> None:
    lines = [f"light={'true' if light else 'false'}", f"reason={' '.join(reason.split())}", f"po={json.dumps(po)}"]
    print("\n".join(lines))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    opts = {}
    for i in range(0, len(args) - 1, 2):
        opts[args[i].lstrip("-")] = args[i + 1]
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if cmd == "classify":
            _emit(*classify(sys.stdin.read().splitlines()))
        elif cmd == "decide":
            _emit(*decide(opts.get("repo", ""), opts.get("base", ""), opts.get("head", ""), token))
        elif cmd == "last-success":
            print(last_success(opts.get("repo", ""), opts.get("workflow", ""), opts.get("branch", ""), token))
        else:
            print(__doc__)
            return 2
    except Exception as e:  # noqa: BLE001 — a classifier that breaks must leave the suite running
        if cmd == "last-success":
            print("")
        else:
            _emit(False, f"the classifier failed ({type(e).__name__})", [])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
