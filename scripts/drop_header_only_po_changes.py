"""Leave out of the nightly commit every staged PO file whose only change is the date of its header.

`bench generate-pot-file` and `bench update-po-files` stamp `POT-Creation-Date` (and may stamp `PO-Revision-Date`)
each time they run. The Translate workflow committed whatever differed, so an app with nothing new to translate still
received one commit a night that moved only that date: every instance then looked behind GitHub each morning, osiris
was 15 commits late on apps nobody had touched, and each fleet update carried the empty commits
(neoffice-maintenance#2179).

Run in the checkout, after `git add` and before the commit: such a file is unstaged and put back as committed.
Prints one line per file left out; exit 0.
"""

import re
import subprocess
import sys

HEADER_DATE = re.compile(r'^"(POT-Creation-Date|PO-Revision-Date): .*\\n"$')


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _without_header_dates(text):
    return [line for line in text.splitlines() if not HEADER_DATE.match(line)]


def header_only(path):
    """True when the staged copy of `path` differs from HEAD by its header dates and nothing else."""
    try:
        before = _git("show", f"HEAD:{path}")
    except subprocess.CalledProcessError:
        return False  # a new file is a change
    after = _git("show", f":{path}")
    return before != after and _without_header_dates(before) == _without_header_dates(after)


def main():
    staged = [p for p in _git("diff", "--cached", "--name-only", "--", "*.po").splitlines() if p]
    for path in staged:
        if header_only(path):
            _git("restore", "--staged", "--worktree", "--", path)
            print(f"only the header date changed in {path}: left out of the commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
