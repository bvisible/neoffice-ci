#!/usr/bin/env python3
"""Does a push change nothing but documentation and translations since the last green run? Then its heavy suite
need not run, and a light check stands in for it (neoffice-maintenance#2558).

Every push used to run the whole suite of its repository, whatever it touched: a sentence added to a skill of
neoffice-devops paid its twenty minutes, a hand-fixed French catalogue of the theme its fifty. A push whose files
are all light is now told apart:

  * any Markdown file (`*.md`), anything under `docs/` or `.claude/`;
  * a translation catalogue of the app, `<anything>/locale/<name>.po` or `.pot`;
  * anything under a folder the calling workflow names with `--also-light` (17.11.2026: the trial pages of
    neoffice-devops, `neoffice_devops/feature_review/pages/`, JSON that no test and no running code reads, pushed by
    every session several times an hour: 57 of its 127 pushes of 16 and 17.11 changed nothing else, and each paid
    the twenty minutes of the suite while the head of main waited for a verdict behind them).

The range is NOT the push's own `before..sha`. It runs from the head of the LAST GREEN run of the branch: pushes
cancel each other's runs (a newer push supersedes the run of the previous one), so a code commit whose run was
cancelled, or which went red, is still in the range of the next push, and that push runs the suite. A head that is
red stays red until a full run says otherwise.

  ci_light_change.py decide --repo OWNER/NAME --base SHA --head SHA [--also-light DIR/,DIR/]
        compares base...head through the GitHub API (GITHUB_TOKEN from the environment) and prints
        `light=true|false`, `reason=<one line>` and `po=<JSON list of the catalogues to read>`;
        appends the same lines to $GITHUB_OUTPUT when it is set.
  ci_light_change.py last-success --repo OWNER/NAME --workflow FILE.yml --branch BRANCH
        prints the head sha of the last successful push run of that workflow on that branch (empty when none).
  ci_light_change.py classify [--also-light DIR/,DIR/] < paths.txt
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


def also_light(value: str) -> tuple[str, ...]:
    """The folders of `--also-light` (comma separated): only relative folder paths, each ending with `/`.

    Anything else is dropped rather than trusted (an absolute path, a `..`, a bare name that would make every path
    beginning with it light): the doubt stays on the side of the suite."""
    out = []
    for part in (value or "").split(","):
        p = part.strip()
        if p and p.endswith("/") and not p.startswith(("/", ".")) and ".." not in p.split("/") and len(p) > 1:
            out.append(p)
    return tuple(dict.fromkeys(out))


def is_light(path: str, also: tuple[str, ...] = ()) -> bool:
    """A path whose content no build reads and no heavy test needs: documentation, a translation catalogue, or a
    file under one of the folders the caller named (`also`)."""
    p = (path or "").strip()
    if not p:
        return False
    if p.lower().endswith(".md"):
        return True
    if p.startswith(("docs/", ".claude/")):
        return True
    if also and p.startswith(also):
        return True
    return bool(_CATALOGUE.search(p))


def is_catalogue(path: str) -> bool:
    return bool(_CATALOGUE.search((path or "").strip()))


def classify(paths: list[str], also: tuple[str, ...] = ()) -> tuple[bool, str, list[str]]:
    """(light, reason, catalogues to read) for a list of changed paths."""
    paths = [p.strip() for p in paths if p and p.strip()]
    if not paths:
        return False, "no changed file to judge", []
    heavy = [p for p in paths if not is_light(p, also)]
    if heavy:
        more = f" and {len(heavy) - 3} more" if len(heavy) > 3 else ""
        return False, f"{len(heavy)} file(s) beyond documentation and translations ({', '.join(heavy[:3])}{more})", []
    po = sorted({p for p in paths if is_catalogue(p)})
    named = sum(1 for p in paths if also and p.startswith(also))
    if named:
        folders = ", ".join(also)
        return True, f"only documentation, translations and {folders} changed ({len(paths)} file(s), {named} there)", po
    return True, f"only documentation and translations changed ({len(paths)} file(s))", po


def judge_comparison(payload: dict | None, also: tuple[str, ...] = ()) -> tuple[bool, str, list[str]]:
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
    light, reason, po = classify(paths, also)
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


def decide(repo: str, base: str, head: str, token: str, also: tuple[str, ...] = ()) -> tuple[bool, str, list[str]]:
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
    return judge_comparison(_get(f"/repos/{repo}/compare/{base}...{head}", token), also)


# How long to wait, and how often, for a listing that has not yet recorded the end of the run before this one.
LISTING_WAITS = (10, 10, 10)


def _green_in(runs: list, current: int | None) -> tuple[str, bool]:
    """(head sha of the newest green run older than `current`, whether an older run still shows as not completed)."""
    for run in runs:  # newest first
        run = run or {}
        rid = run.get("id")
        if current is not None and isinstance(rid, int) and rid >= current:
            continue  # this run, and the newer pushes waiting behind it
        if run.get("status") != "completed":
            return "", current is not None
        if run.get("conclusion") == "success":
            sha = run.get("head_sha", "")
            return (sha if _valid_sha(sha) else ""), False
    return "", False


def last_success(repo: str, workflow: str, branch: str, token: str, current: int | None = None,
                 waits: tuple = LISTING_WAITS) -> str:
    """The head sha of the last successful push run of `workflow` on `branch`, empty when none.

    17.11.2026: a push of trial pages waited behind a run of code; that run went green at 11:40:20 UTC, the waiting run
    started at once, and two seconds later the listing filtered on `status=success` still gave the green before it:
    the code of that run was counted in the range, and the pages paid the whole suite. With one run of a branch at a
    time (the callers' concurrency), a run OLDER than this one (`current`, GITHUB_RUN_ID) that the listing shows as
    not completed is a listing that has not caught up: read it again a few times. The unfiltered listing is read first;
    the filtered one stays as the fallback (a green older than the page). Any doubt leaves the sha empty, and the suite
    runs."""
    if not (_valid_repo(repo) and re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow or "") and branch and token):
        return ""
    if current is None:
        raw = os.environ.get("GITHUB_RUN_ID", "")
        current = int(raw) if raw.isdigit() else None
    path = f"/repos/{repo}/actions/workflows/{workflow}/runs?"
    q = urllib.parse.urlencode({"branch": branch, "event": "push", "per_page": 50})
    for attempt in range(len(waits) + 1):
        data = _get(path + q, token)
        if data is None:
            break
        sha, behind = _green_in((data or {}).get("workflow_runs") or [], current)
        if sha:
            return sha
        if not behind:
            break
        if attempt == len(waits):
            return ""  # still not caught up: no base, the suite runs
        time.sleep(waits[attempt])
    q = urllib.parse.urlencode({"branch": branch, "event": "push", "status": "success", "per_page": 1})
    data = _get(path + q, token)
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
    also = also_light(opts.get("also-light", ""))
    try:
        if cmd == "classify":
            _emit(*classify(sys.stdin.read().splitlines(), also))
        elif cmd == "decide":
            _emit(*decide(opts.get("repo", ""), opts.get("base", ""), opts.get("head", ""), token, also))
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
