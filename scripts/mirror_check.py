"""Refuse an upstream preview whose caller does not mirror the normal one.

The preview measures the cost of the fork upgrade (tracker #138) by running the
SAME tests on a bench built from upstream frappe/erpnext. That answer is only
worth anything if the two callers agree on everything else: the moment one of
them passes an input the other does not, the preview stops measuring the upstream
and starts measuring the difference between two workflow files -- and it reports
that difference as the price of the upgrade, which is the most expensive kind of
wrong answer, because it is believed.

Twice in one day, 2026-09-10:

* `insights` -- the preview lacked `build_frappe_assets: true`, so a test that
  renders a web page died on a manifest the bench never built.
* `hrms` -- the preview lacked `erpnext_bootstrap: false`, so erpnext's setup
  wizard ran on top of hrms's own and took the default company (`Wind Power LLC`
  instead of `_Test Company`). 24 tests reported as "the cost of the upgrade";
  every one of them was ours.

Two inputs are allowed to differ, and only these:

* `upstream_preview` -- the flag that makes it a preview at all;
* `test_args` -- a `workflow_dispatch` input the preview does not expose.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NORMAL = Path(".github/workflows/tests.yml")
PREVIEW = Path(".github/workflows/upstream-preview.yml")
ALLOWED_TO_DIFFER = {"upstream_preview", "test_args"}


def caller_inputs(path: Path) -> dict[str, str] | None:
    """The `with:` block of the reusable-workflow call, as a plain dict."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf8")
    if "    with:" not in text:
        return {}
    block = text.split("    with:", 1)[1]
    # the block ends at the next key of the same job (secrets:, needs:, if:…)
    end = re.search(r"^    [a-z_]+:", block[1:], re.M)
    if end:
        block = block[: end.start() + 1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"\s+([a-z_]+):\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main() -> int:
    normal = caller_inputs(NORMAL)
    preview = caller_inputs(PREVIEW)
    if preview is None:
        print("no upstream-preview.yml here — nothing to mirror")
        return 0
    if normal is None:
        print(f"::warning::{PREVIEW} exists but {NORMAL} does not — cannot compare")
        return 0

    keys = (set(normal) | set(preview)) - ALLOWED_TO_DIFFER
    bad = [(k, normal.get(k), preview.get(k)) for k in sorted(keys) if normal.get(k) != preview.get(k)]
    if not bad:
        print(f"the two callers agree on {len(keys)} input(s) — the preview measures upstream, not itself")
        return 0

    print("The upstream preview does NOT mirror the normal CI. Differing inputs:\n")
    for key, a, b in bad:
        print(f"  {key}:")
        print(f"      tests.yml            = {a if a is not None else '(absent)'}")
        print(f"      upstream-preview.yml = {b if b is not None else '(absent)'}")
    print(
        "\nA preview that differs measures ITS OWN gap and reports it as the cost of the\n"
        "fork upgrade. Copy the input across (or add it to ALLOWED_TO_DIFFER in\n"
        "neoffice-ci/scripts/mirror_check.py if it genuinely must differ, and say why)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
