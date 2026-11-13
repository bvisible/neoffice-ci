#!/usr/bin/env python3
"""What the tracker issue of a red run says: the tests that failed, not the last lines of the log.

The report step of frappe-app-ci.yml used to send `tail -c 1400` of the server test log to the hub, which posts it
on the repository's `[CI]` issue. A test's prints go to stdout and unittest's verdict to stderr, so in that log the
verdict (`FAIL: …`, `Ran N tests`, `FAILED (…)`) comes BEFORE the prints of the last tests: on 2026-11-13 the theme
was red for two hours and its issue quoted 800 characters of test-site setup, not one failing test. The JavaScript
and pytest suites did not even write to that log: a red `node --test` reported the end of a green server suite.

This reads every log it is given and prints, for each suite it recognises, its verdict line and then the failing
tests, deduplicated, with short names:

  - unittest, as `bench run-tests` prints it (`FAIL: test_x (pkg.module.Class.test_x)`, `Ran N tests`,
    `FAILED (failures=…)`) and as `bench run-parallel-tests` prints it (` FAIL  test_x (…)`, `<…TestResult run=…>`);
  - pytest (`FAILED path::test - …`, `3 failed, 120 passed in 4.56s`);
  - `node --test`, TAP (Node 22 without a terminal: `not ok 18 - name`, `# fail 3`) and spec (`✖ failing tests:`,
    `ℹ fail 3`, the default from Node 23).

Only names and counts are copied: never a traceback, an error message, a subtest's parameters or a pytest id's
parameters (they hold values). An error's exception class is kept (`DoesNotExistError`), a plain assertion's is not.
When no failing test is found in any log (the suite was killed, a format this does not know), the end of the log is
printed after the verdicts, as before: the last resort, not the rule.

  ci_test_summary.py [--limit N] LOG [LOG ...]

A log that does not exist is skipped; with none at all, nothing is printed (the caller then says the run failed
before the tests). Exit 0 on every handled path: the report must never turn a run red, and the caller falls back on
`tail` when this fails.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import OrderedDict

DEFAULT_LIMIT = 1400
NAME_LIMIT = 140

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# unittest (bench run-tests) and frappe's parallel runner, whose flavour is a padded " FAIL " without a colon. The
# progress dots of the same stream may precede the line. Python >= 3.11 prints `name (module.Class.name)`, before
# that `name (module.Class)`; a subtest adds ` (param=value)` or ` [message]` after it.
_UT_HEAD = re.compile(r"^[.sEFxu]*\s*(FAIL|ERROR):?\s+([A-Za-z_]\w*) \(([A-Za-z_][\w.]*)\)(.*)$")
_UT_SEP1 = re.compile(r"^={20,}\s*$")
_UT_RAN = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s\s*$")
_UT_VERDICT = re.compile(r"^(FAILED|OK)(?: \(([^)]*)\))?\s*$")
_UT_RESULT = re.compile(r"^<[\w.]*TestResult run=(\d+) errors=(\d+) failures=(\d+)>\s*$")
# The last line of a traceback, at column 0: `frappe.exceptions.DoesNotExistError: …` or `KeyError`.
_EXC_LINE = re.compile(
    r"^(?:[A-Za-z_]\w*\.)*([A-Z]\w*(?:Error|Exception|Exit|Interrupt|Warning|Failure|Timeout))(?::|\s*$)"
)

_PY_FAIL = re.compile(r"^(FAILED|ERROR) (\S+?)(?: - (.*))?\s*$")
_PY_TOTAL = re.compile(
    r"^=*\s*((?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|deselected|warnings?|rerun)(?:, )?)+)"
    r" in [\d.]+s(?: \([\d:.]+\))?\s*=*\s*$"
)
_PY_EXC = re.compile(r"^(?:[A-Za-z_]\w*\.)*([A-Z]\w*(?:Error|Exception|Exit|Interrupt|Warning|Failure|Timeout))(?::|$)")

_TAP_NOT_OK = re.compile(r"^(\s*)not ok \d+ - (.*?)\s*$")
_TAP_DIRECTIVE = re.compile(r"\s+#\s*(?:SKIP|TODO)\b.*$", re.IGNORECASE)
_TAP_COUNT = re.compile(r"^# (tests|suites|pass|fail|cancelled|skipped|todo) (\d+)\s*$")
_SPEC_COUNT = re.compile(r"^ℹ (tests|suites|pass|fail|cancelled|skipped|todo) (\d+)\s*$")
_SPEC_SECTION = re.compile(r"^✖ failing tests:\s*$")
_SPEC_AT = re.compile(r"^\s*test at (\S+)\s*$")
_SPEC_FAIL = re.compile(r"^(\s*)✖ (.*?)(?: \([\d.]+m?s\))?\s*$")
_SPEC_EXC = re.compile(r"^\s+([A-Z]\w*(?:Error|Exception|Failure))\b")

ASSERTION = "AssertionError"


class Section:
    """One suite found in a log: its verdict line and its failing tests, in the order they were printed."""

    def __init__(self, title: str):
        self.title = title
        self.verdict = ""
        self.green = False
        self.failures: OrderedDict[tuple[str, str], dict] = OrderedDict()

    def add(self, kind: str, name: str, detail: str = "", subtest: bool = False) -> None:
        # Deduplicated on the whole name: two long names that start alike are two tests, cut only when shown.
        name = " ".join(name.split())
        entry = self.failures.setdefault((kind, name), {"detail": "", "subtests": 0})
        if detail and detail != ASSERTION and not entry["detail"]:
            entry["detail"] = detail
        if subtest:
            entry["subtests"] += 1

    def lines(self) -> list[str]:
        out = []
        for (kind, name), entry in self.failures.items():
            line = f"- {kind} {_short(name)}"
            if entry["subtests"]:
                n = entry["subtests"]
                line += f" ({n} sous-test{'s' if n > 1 else ''})"
            if entry["detail"]:
                line += f" → {entry['detail']}"
            out.append(line)
        return out

    def header(self) -> str:
        return f"{self.title} : {self.verdict}" if self.verdict else f"{self.title} :"


def _short(name: str) -> str:
    name = " ".join(name.split())
    if len(name) > NAME_LIMIT:
        name = name[: NAME_LIMIT - 1].rstrip() + "…"
    return name


def _basename_location(location: str) -> str:
    """`/home/runner/…/nora/tests/js/bar_chat.test.js:261:1` -> `bar_chat.test.js:261`."""
    location = location.strip().strip("'\"")
    m = re.match(r"^(.*?):(\d+)(?::\d+)?$", location)
    path, line = (m.group(1), m.group(2)) if m else (location, "")
    base = os.path.basename(path.replace("\\", "/")) or path
    return f"{base}:{line}" if line else base


def read_lines(path: str) -> list[str] | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    text = _ANSI.sub("", data.decode("utf-8", "replace"))
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


# ---------------------------------------------------------------------------------------------------------- unittest


def _unittest_name(name: str, test_id: str) -> str:
    parts = test_id.split(".")
    if "_FailedTest" in parts:  # a module that does not import: `name` is the module
        return name
    full = parts if parts[-1] == name else parts + [name]
    return ".".join(full[-3:])


def parse_unittest(lines: list[str]) -> Section | None:
    section = Section("Tests serveur (unittest)")
    found = False
    i = 0
    n = len(lines)
    verdicts = []
    while i < n:
        line = lines[i]
        head = _UT_HEAD.match(line)
        if head:
            kind, name, test_id, rest = head.groups()
            # The cause of an error is the last exception line of its block, before the next separator.
            exc = ""
            j = i + 1
            while j < n and not _UT_SEP1.match(lines[j]) and not _UT_RAN.match(lines[j]):
                m = _EXC_LINE.match(lines[j])
                if m:
                    exc = m.group(1)
                j += 1
            section.add(kind, _unittest_name(name, test_id), exc if kind == "ERROR" else "", subtest=bool(rest.strip()))
            found = True
            i = j
            continue
        ran = _UT_RAN.match(line)
        if ran:
            verdict = f"Ran {ran.group(1)} tests in {ran.group(2)}s"
            for k in range(i + 1, min(i + 4, n)):
                v = _UT_VERDICT.match(lines[k].strip())
                if v:
                    verdict += f" · {v.group(1)}" + (f" ({v.group(2)})" if v.group(2) else "")
                    break
            verdicts.append(verdict)
            found = True
        i += 1
    if not verdicts:
        for line in lines:
            r = _UT_RESULT.match(line.strip())
            if r:
                verdicts.append(f"run={r.group(1)} errors={r.group(2)} failures={r.group(3)}")
                found = True
    if not found:
        return None
    section.verdict = " | ".join(verdicts[-3:])
    section.green = bool(verdicts) and not section.failures and all(
        " · OK" in v or v.endswith("errors=0 failures=0") for v in verdicts
    )
    return section


# ------------------------------------------------------------------------------------------------------------ pytest


def parse_pytest(lines: list[str]) -> Section | None:
    section = Section("pytest")
    found = False
    totals = []
    for line in lines:
        m = _PY_FAIL.match(line)
        if m and ("::" in m.group(2) or m.group(2).endswith(".py")):
            kind = "FAIL" if m.group(1) == "FAILED" else "ERROR"
            test_id = m.group(2)
            param = "[" in test_id
            test_id = re.sub(r"\[.*$", "", test_id)  # a parametrised id holds values
            pieces = test_id.split("::")
            pieces[0] = os.path.basename(pieces[0])
            exc = _PY_EXC.match((m.group(3) or "").strip())
            section.add(kind, "::".join(pieces), exc.group(1) if exc else "", subtest=param)
            found = True
            continue
        t = _PY_TOTAL.match(line.strip())
        if t:
            totals.append(t.group(1).rstrip(", "))
            found = True
    if not found:
        return None
    section.verdict = " | ".join(totals[-3:])
    section.green = bool(totals) and not section.failures and all(
        "failed" not in t and "error" not in t for t in totals
    )
    return section


# ------------------------------------------------------------------------------------------------------- node --test


def _tap_unescape(name: str) -> str:
    return name.replace("\\#", "#").replace("\\\\", "\\")


def parse_node(lines: list[str]) -> Section | None:
    section = Section("Tests JS (node --test)")
    counts: OrderedDict[str, str] = OrderedDict()
    found = False
    n = len(lines)

    # TAP (Node 22 when its output is not a terminal, as on a runner).
    i = 0
    while i < n:
        m = _TAP_NOT_OK.match(lines[i])
        if not m or _TAP_DIRECTIVE.search(m.group(2)):
            c = _TAP_COUNT.match(lines[i])
            if c:
                counts[c.group(1)] = c.group(2)
            i += 1
            continue
        indent, name = len(m.group(1)), _tap_unescape(m.group(2))
        meta = {}
        j = i + 1
        if j < n and lines[j].strip() == "---":
            j += 1
            while j < n and lines[j].strip() != "...":
                line = lines[j]
                own = len(line) - len(line.lstrip(" "))
                if own == indent + 2 and ":" in line:
                    key, _, value = line.strip().partition(":")
                    meta.setdefault(key, value.strip().strip("'\""))
                j += 1
        found = True
        if meta.get("failureType") != "subtestsFailed":
            label = os.path.basename(name) if name.startswith("/") else name
            if meta.get("location"):
                label = f"{_basename_location(meta['location'])} — {label}"
            section.add("FAIL", label, meta.get("name", ""))
        i = j + 1

    # spec (the default from Node 23, and Node 22 on a terminal): only its closing list, which names the failing
    # tests and leaves out the suites that fail because of them; the same tests are drawn earlier in the tree.
    in_list = False
    location = ""
    for k, line in enumerate(lines):
        c = _SPEC_COUNT.match(line)
        if c:
            counts[c.group(1)] = c.group(2)
            found = True
            continue
        if _SPEC_SECTION.match(line):
            in_list, found = True, True
            continue
        if not in_list:
            continue
        at = _SPEC_AT.match(line)
        if at:
            location = at.group(1)
            continue
        f = _SPEC_FAIL.match(line)
        if f and f.group(2):
            exc = ""
            for nxt in lines[k + 1 : k + 4]:
                if not nxt.strip():
                    continue
                e = _SPEC_EXC.match(nxt)
                exc = e.group(1) if e else ""
                if "subtests failed" in nxt or "subtest failed" in nxt:
                    exc = None
                break
            if exc is not None:
                label = f"{_basename_location(location)} — {f.group(2)}" if location else f.group(2)
                section.add("FAIL", label, exc)
            location = ""

    if not found and not counts:
        return None
    shown = [f"{key} {counts[key]}" for key in ("tests", "pass", "fail") if key in counts]
    if counts.get("cancelled", "0") != "0":  # a file that timed out or crashed: its tests are cancelled, not failed
        shown.append(f"cancelled {counts['cancelled']}")
    section.verdict = " · ".join(shown)
    section.green = bool(counts) and not section.failures and counts.get("fail", "0") == "0" and (
        counts.get("cancelled", "0") == "0"
    )
    return section


# ----------------------------------------------------------------------------------------------------------- summary


def _tail(lines: list[str], budget: int) -> str:
    text = "\n".join(lines).rstrip()
    if budget <= 0 or not text:
        return ""
    if len(text) <= budget:
        return text
    cut = text[-budget:]
    newline = cut.find("\n")
    return cut[newline + 1 :] if 0 <= newline < len(cut) - 1 else cut


def _more(count: int) -> str:
    s = "s" if count > 1 else ""
    return f"… et {count} autre{s} test{s} en échec : lire le run."


def summarize(paths: list[str], limit: int = DEFAULT_LIMIT) -> str:
    logs = [lines for lines in (read_lines(p) for p in paths) if lines is not None]
    if not logs:
        return ""

    per_log = [[s for s in (parse(lines) for parse in (parse_unittest, parse_pytest, parse_node)) if s] for lines in logs]
    sections = [s for found in per_log for s in found]
    headers = [s.header() for s in sections]
    failing = sum(len(s.failures) for s in sections)

    if not failing:
        # The last resort: the verdicts that were found, then the end of the log of the first suite that is not
        # green (a suite killed mid-run has no verdict at all), else of the first log.
        source = next((lines for lines, found in zip(logs, per_log) if not any(s.green for s in found)), logs[0])
        out = headers + ["Aucun test en échec reconnu dans le journal ; sa fin :"]
        out.append(_tail(source, limit - sum(len(x) + 1 for x in out)))
        return "\n".join(x for x in out if x)[:limit]

    # The verdicts are always shown; the failing tests fill what is left, keeping room for the line that counts
    # the ones left out.
    used = sum(len(h) + 1 for h in headers)
    reserve = len(_more(failing)) + 1
    out, shown = [], 0
    for s in sections:
        out.append(s.header())
        for line in s.lines():
            room = limit - used - (0 if shown + 1 == failing else reserve)
            if len(line) + 1 > room:
                continue
            out.append(line)
            used += len(line) + 1
            shown += 1
    if shown < failing:
        out.append(_more(failing - shown))
    return "\n".join(out)[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="characters at most (the hub keeps 1500)")
    parser.add_argument("logs", nargs="+", help="test logs; a missing one is skipped")
    args = parser.parse_args(argv)
    summary = summarize(args.logs, max(200, args.limit))
    if summary:
        sys.stdout.write(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
