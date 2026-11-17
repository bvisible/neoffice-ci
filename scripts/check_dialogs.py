#!/usr/bin/env python3
"""A new window of the desk is the desk's window: found without running anything.

Every window of the desk is one model, whatever app opens it (bvisible/neoffice-design-system, readme §6.18): a
`frappe.ui.Dialog`, which our fork of Frappe draws (`frappe/public/js/frappe/ui/dialog_model.js`) centred, in one of
four sizes chosen by what one does in it (small / default / large / extra-large = 440 / 560 / 800 / 1 140 px), with one
ink action named by its verb, the same keys and the same rules for leaving. Measured on 16.11.2026, before the model:
482 dialogs in our apps, 13 widths, 14 step bars for 16 wizards, windows that a click beside closed in the middle of a
typing. A window gets the model by being a `frappe.ui.Dialog` and saying its family: this check stops a new one from
leaving it again.

The rules, each named by the id that a message, the inventory and an exception use:

  size-missing    A dialog with more than 4 fields that hold a value (any fieldtype but HTML, Button, Section Break,
                  Column Break, Tab Break, Heading and Fold; a field `hidden: 1` does not count) declares
                  `size: "large"` (L, 800 px: a form) or `"extra-large"` (XL); `"small"` is a question or a message. A
                  `size` that is not small, large or extra-large (`"medium"`, `"xl"`) is refused too. Frappe widens by
                  itself a dialog whose section holds two Column Breaks: that one is not refused.
  vague-verb      The ink action says what it does: `primary_action_label` is never « OK », « Valider », « Submit »,
                  « Confirm », « Confirmer », « Oui », « Yes », « Non », « No » (any case, bare or in `__()`), except
                  « OK » on a message (a dialog with no field a person types into, HTML and Heading only, or none). A
                  `primary_action` with no label on a dialog that takes input is refused too: Frappe names it « Submit »
                  (« Valider »). So is `d.set_primary_action(__("Submit"), ...)` on the dialog's variable, in the
                  FOLLOW_LINES lines after it and before the next window of the file.
  hand-made-veil  No veil or modal made by hand, on an added line: an element fixed over the whole screen
                  (`position: fixed` with `inset: 0`, or with left and right 0 or a width of 100% / 100vw, and top and
                  bottom 0 or a height of 100% / 100vh), in a CSS rule, a style object or an inline style; a Bootstrap
                  modal built in a script (`class="modal"` markup, `.modal("show")`, `.modal({...})`,
                  `new bootstrap.Modal`, `frappe.get_modal(...)`). Frappe's own `.modal` and `.modal-backdrop` are not
                  ours: a rule that restyles them is not a veil. Nor is a drawer (fixed, full height, a width of its
                  own), a bar (fixed, full width, a height of its own), or a layer every click goes through
                  (`pointer-events: none`: a highlight, an ink).
  own-width       A window's width is its `size`, on an added line: no `.find(".modal-dialog").css("max-width", ...)`
                  (`width`, `min-width`, an object with one of them, `.width(...)`, `.style.maxWidth = ...`), no
                  `addClass("modal-lg" | "modal-xl" | "modal-sm")`, no CSS rule that gives `.modal-dialog`,
                  `.modal-content`, `.modal-lg`, `.modal-xl`, `.modal-sm`, or a class the same change adds to
                  `.modal-dialog`, a width.
                  The portal (`www/`, `templates/`) is not judged by these two: the pages a customer sees have no
                  desk behind them, and the model is loaded by the desk only.

`new frappe.ui.form.MultiSelectDialog(...)` is listed, and judged by the last two rules only: its fields and its action
are its own.

It judges the change, not the stock: a dialog is judged when a line of its source is new or changed (or a line beside it
was removed), a veil or a width when a line that makes it is added; a change of indentation is no change. The stock
blocks nobody until someone touches it, and `--inventory` lists it. A range that holds a merge is not judged: on a fork,
a merge of upstream brings their windows.

What it reads is the options object written in the call. Fields given by a variable are read when that variable is an
array declared earlier in the same file. What it cannot read is never refused, a doubt never refuses: options built
elsewhere or spread (`...opts`), fields that a function returns, a label held in a variable, a style set property by
property (`el.style.position = "fixed"`), a class and its CSS rule whose two halves are not in the same change.
The model's own files (`dialog_model.*`), bundles (`assets/<name>-<hash>.js`, lines of a bundler's length), vendored
libraries (`public/js/lib/`, `vendor/`), `node_modules/`, `dist/`, `build/`, `cypress/`, `public/craft/`, `*.min.*`, and
the `docs/` and `design/` of a repository (mock-ups) are not read. Nothing is imported or executed.

An exception names its rule and says why, on the `new frappe.ui.Dialog(` line, on the offending line, or on the line
above either (in CSS, on any line of the rule or the line above it):

    // dialog-check: ok hand-made-veil the till takes the whole screen, there is no desk behind it
    /* dialog-check: ok own-width the floor plan of the shop is drawn at its own scale */

Several rules: `dialog-check: ok size-missing,vague-verb <reason>`. A marker that names no rule of this list, or gives
no reason of at least MIN_REASON characters, excuses nothing and is refused itself.

    check_dialogs.py --base <revision>         every window changed since that revision (CI)
    check_dialogs.py --staged                  what is staged, against HEAD
    check_dialogs.py [--base HEAD] file...     these files as they are on disk (pre-commit)
    check_dialogs.py --inventory [--json]      every window of the repository and what it breaks, nothing is refused

Exit 1 when something is refused.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field

OK_MARK = "dialog-check: ok"
MIN_REASON = 10  # a reason, not "ok" or "n/a"
DIALOG_RULES = ("size-missing", "vague-verb")
LINE_RULES = ("hand-made-veil", "own-width")
RULES = DIALOG_RULES + LINE_RULES
MARKER = "marker"  # the id of a marker refused for itself
SIZES = {"small", "large", "extra-large"}
FORM_SIZES = {"large", "extra-large"}
SHORT_ENTRY = 4  # M, 560 px: a short entry, 1 to 4 fields
NOT_VALUES = {"HTML", "Button", "Section Break", "Column Break", "Tab Break", "Heading", "Fold"}
VAGUE = {"ok", "valider", "submit", "confirm", "confirmer", "oui", "yes", "non", "no"}
FOLLOW_LINES = 40  # how far after a dialog a call on its variable is still read as its own
SCRIPTS = {".js", ".mjs", ".ts", ".jsx", ".tsx"}
MARKUPS = {".vue", ".html"}
STYLES = {".css", ".scss"}
SKIP_PARTS = (
    "/node_modules/",
    "/dist/",
    ".min.",
    "/public/craft/",
    "/build/",
    "/cypress/",
    "/vendor/",
    "/vendors/",
    "/public/js/lib/",
    "/public/js/libs/",
    "/public/lib/",
    "/public/libs/",
)
SKIP_TOP = ("docs/", "design/")  # documentation and mock-ups at the root of a repository: no window of the desk
BUILT = re.compile(r"/assets/[^/]+-[\w-]{8}\.(?:m?js|css)$")  # what a bundler wrote: `assets/index-DLplWssb.js`
PORTAL = re.compile(r"/(?:www|templates)/")  # the pages a customer sees: no desk, no model behind them
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "cypress", ".tox", ".mypy_cache"}
MODEL_FILE = re.compile(r"^dialog_model\.")  # the model itself: it draws the sizes the others must not draw
BUNDLED_LINE = 400  # characters per line on average: a file written by a bundler, not by a person


# ---------------------------------------------------------------------------- reading a source


_SCRIPT_STOP = re.compile(r"['\"`/{}]")
_DOUBLE = re.compile(r'(?:[^"\\\n]|\\.)*', re.S)
_SINGLE = re.compile(r"(?:[^'\\\n]|\\.)*", re.S)
_TEMPLATE = re.compile(r"(?:[^`\\$]|\\.|\$(?!\{))*", re.S)
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^}")
_KEYWORD_BEFORE = re.compile(r"(?:^|[^\w$])(?:return|typeof|case|in|of|delete|void|throw|new|else|do|yield|await)$")


def _string_end(text: str, j: int, b: int) -> int:
    """The offset of the quote that closes the string opened at j (a string never runs past its line)."""
    found = (_DOUBLE if text[j] == '"' else _SINGLE).match(text, j + 1, b)
    return found.end()


def _template(text: str, i: int, b: int, strings: list, stack: list) -> int:
    """From inside a template literal: its text up to the closing backtick, or up to a `${`, whose code follows."""
    end = _TEMPLATE.match(text, i, b).end()
    strings.append((i, end))
    if end >= b:
        return b
    if text[end] == "$":
        stack.append(0)
        return end + 2
    return end + 1


def _starts_regex(text: str, a: int, j: int) -> bool:
    """A `/` that opens a regular expression, not a division: what comes before it says so."""
    k = j - 1
    while k >= a and text[k] in " \t\r\n":
        k -= 1
    if k < a or text[k] in _REGEX_BEFORE:
        return True
    return bool(_KEYWORD_BEFORE.search(text, max(a, k - 10), k + 1))


def _regex_end(text: str, j: int, b: int) -> int:
    k, in_class = j + 1, False
    while k < b and text[k] != "\n":
        ch = text[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return k
        k += 1
    return k


def _lex_script(text: str, a: int, b: int, comments: list, strings: list) -> None:
    """The comments and the contents of the strings, template literals and regular expressions of text[a:b]."""
    i, stack = a, []  # one brace depth per open `${`
    while i < b:
        found = _SCRIPT_STOP.search(text, i, b)
        if not found:
            return
        j, ch = found.start(), found.group()
        if ch == "{":
            if stack:
                stack[-1] += 1
            i = j + 1
        elif ch == "}":
            if stack and stack[-1] == 0:
                stack.pop()
                i = _template(text, j + 1, b, strings, stack)
                continue
            if stack:
                stack[-1] -= 1
            i = j + 1
        elif ch in "'\"":
            end = _string_end(text, j, b)
            strings.append((j + 1, end))
            i = end + 1
        elif ch == "`":
            i = _template(text, j + 1, b, strings, stack)
        elif text.startswith("//", j):
            end = text.find("\n", j, b)
            end = b if end < 0 else end
            comments.append((j, end))
            i = end
        elif text.startswith("/*", j):
            end = text.find("*/", j + 2, b)
            end = b if end < 0 else end + 2
            comments.append((j, end))
            i = end
        elif _starts_regex(text, a, j):
            end = _regex_end(text, j, b)
            strings.append((j + 1, end))
            i = end + 1
        else:
            i = j + 1


def _lex_style(text: str, a: int, b: int, comments: list) -> None:
    for found in re.finditer(r"/\*.*?(?:\*/|\Z)|(?:(?<=[\s;{}])|^)//[^\n]*", text[a:b], re.S | re.M):
        comments.append((a + found.start(), a + found.end()))


def _blank(chars: list[str], ranges: list[tuple[int, int]], fill: str) -> None:
    for a, b in ranges:
        if b > a:
            chars[a:b] = [c if c == "\n" else fill for c in chars[a:b]]


def _matching(code: str, i: int) -> int:
    """The offset of the bracket that closes the one at i, len(code) when it never closes."""
    depth = 0
    for found in _BRACKETS.finditer(code, i):
        if found.group() in "([{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return found.start()
    return len(code)


_BRACKETS = re.compile(r"[()\[\]{}]")
_BRACKETS_OR_COMMA = re.compile(r"[()\[\]{},]")


def _split_top(code: str, a: int, b: int) -> list[tuple[int, int]]:
    """The spans of code[a:b] between its commas that stand outside any bracket."""
    parts, depth, start = [], 0, a
    for found in _BRACKETS_OR_COMMA.finditer(code, a, b):
        ch = found.group()
        if ch == ",":
            if depth == 0:
                parts.append((start, found.start()))
                start = found.end()
        elif ch in "([{":
            depth += 1
        else:
            depth -= 1
    parts.append((start, b))
    return [(x, y) for x, y in parts if code[x:y].strip()]


def _norm(label: str) -> str:
    return label.strip().rstrip(".!…").strip().lower()


def _shown(selector: str) -> str:
    """A selector as a message shows it: on one line, and its last part when it is long."""
    selector = " ".join(selector.split())
    return selector if len(selector) <= 80 else "…" + selector.rsplit(",", 1)[-1].strip()[-80:]


def _camel_to_kebab(name: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1-\2", name).lower()


_MARK = re.compile(re.escape(OK_MARK) + r"(.*)")


@dataclass
class Marker:
    rules: set[str]
    valid: bool


def _marker(line: str) -> Marker | None:
    found = _MARK.search(line)
    if not found:
        return None
    rest = re.sub(r"\s*(?:\*/|-->|%\}|\}\}).*$", "", found.group(1)).strip()
    head, _, reason = rest.partition(" ")
    rules = {one for one in head.split(",") if one}
    return Marker(rules, bool(rules) and rules <= set(RULES) and len(reason.strip()) >= MIN_REASON)


# ---------------------------------------------------------------------------- what a file holds


@dataclass
class Fields:
    read: bool = True  # every element was read: none is a spread, a call, a variable or a guess
    values: int = 0  # visible fields that hold a value, read-only ones too: what the window must make room for
    typed: int = 0  # of those, the ones a person types into: a window without one is a message
    columns: int = 0  # the most Column Breaks of one section: Frappe widens the window by it (set_modal_size)


@dataclass
class Window:
    kind: str  # Dialog or MultiSelectDialog
    start: int  # offset of `new`
    end: int  # offset of the closing `)`
    line: int
    end_line: int
    var: str = ""  # the variable it is given to, "" when it is given to none
    options: dict[str, tuple[int, int, int]] | None = None  # key: (value start, value end, key offset)
    spread: bool = False  # its options spread another object: what is not written may be there
    title: str = ""
    size: str | None = None  # the size it declares, None when it declares none
    fields: Fields | None = None
    follow_end: int = 0  # where the calls on its variable stop being its own


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    what: str
    lines: frozenset[int]  # the lines that make it: it is judged when one of them changed
    anchors: frozenset[int]  # where a marker excuses it (the line above each counts too)
    window: int = 0  # the line of the window it belongs to, 0 when it stands alone
    title: str = ""


@dataclass
class Block:
    selector: str
    start: int  # offset where the rule (or the object) starts
    end: int
    declarations: list[tuple[str, str, int]] = field(default_factory=list)  # (property, value, offset)


_NEW_WINDOW = re.compile(r"\bnew\s+frappe\s*\.\s*ui\s*\.\s*(Dialog|form\s*\.\s*MultiSelectDialog)\s*\(")
_ASSIGNED = re.compile(r"([\w$]+(?:\s*\.\s*[\w$]+)*)\s*=\s*$")
_KEY = re.compile(r"\s*(?:([\w$]+)|([\"'])(_*)\2)\s*:(?!:)")
_METHOD = re.compile(r"\s*(?:async\s+)?\*?\s*([\w$]+)\s*\(")
_SHORTHAND = re.compile(r"\s*([\w$]+)\s*$")
_SPREAD = re.compile(r"\s*\.\.\.")
_STRING = re.compile(r"""(__\s*\(\s*)?(["'`])(_*)\2""")
_ANY_STRING = re.compile(r"""(["'`])(_*)\1""")
_CHAINED = re.compile(r"\s*\.")
_DECLARATION = re.compile(r"(?:^|[;,{\n])\s*([\"'`]?)([A-Za-z][\w-]*)\1\s*:\s*([^;,\n}]*)")
_INNER_BLOCK = re.compile(r"\{([^{}]{0,2000})\}")
_SELECTOR_CUT = re.compile(r"[;{}()'\"`=,\n]")
_INLINE_STYLE = re.compile(
    r"""\bstyle(?:\s*\.\s*cssText)?\s*=\s*\\?(?:"([^"\\]{0,800})|'([^'\\]{0,800})|`([^`\\]{0,800}))"""
)
_FRAPPE_MODAL = re.compile(r"\.modal(?:-backdrop)?(?![\w-])")
_WINDOW_CLASS = re.compile(r"\.modal-(?:dialog|content|lg|xl|sm)(?![\w-])")
_SIZE_CLASSES = {"modal-lg", "modal-xl", "modal-sm"}
_WIDTHS = {"width", "max-width", "min-width"}
_NODE_CALL = re.compile(
    r"""["'`][^"'`\n]*\.modal-(?:dialog|content)(?![\w-])[^"'`\n]*["'`]\s*\)\s*\.\s*(css|addClass|toggleClass|width)\s*\("""
)
_NODE_STYLE = re.compile(
    r"""["'`][^"'`\n]*\.modal-(?:dialog|content)(?![\w-])[^"'`\n]*["'`]\s*\)\s*\.\s*style\s*"""
    r"""(?:\.\s*(?:maxWidth|minWidth|width)\s*=(?!=)|\.\s*setProperty\s*\(\s*["'`](?:max-|min-)?width["'`])"""
)
_CLASS_CALL = re.compile(
    r"""(?:\.\s*(?:addClass|toggleClass)|classList\s*\.\s*(?:add|toggle))\s*\(\s*["'`]([^"'`]*)["'`]"""
)
_MODAL_MARKUP = re.compile(r"""\bclass(?:Name)?\s*[=:]\s*\\?["'`]([^"'`\\]*)""")
_MODAL_CALLS = (
    (re.compile(r"""\.\s*modal\s*\(\s*["'`]show["'`]\s*\)"""), '.modal("show") opens a Bootstrap modal by hand'),
    (re.compile(r"\.\s*modal\s*\(\s*\{"), ".modal({...}) sets a window's veil and keys by hand: they are the model's"),
    (re.compile(r"\bnew\s+bootstrap\s*\.\s*Modal\b|\bbootstrap\s*\.\s*Modal\s*\.\s*getOrCreateInstance\b"),
     "a Bootstrap modal built by hand"),
    (re.compile(r"\bfrappe\s*\.\s*get_modal\s*\("), "frappe.get_modal() is a bare Bootstrap modal, outside the model"),
)
_ZERO = {"0", "0px", "0%", "0rem", "0em"}
_FULL_WIDTH = {"100vw", "100%", "100dvw", "100svw", "100lvw"}
_FULL_HEIGHT = {"100vh", "100%", "100dvh", "100svh", "100lvh"}


def _value(raw: str) -> str:
    value = raw.strip().strip("\"'`").strip()
    return re.sub(r"\s*!important$", "", value).strip().lower()


def _all_zero(value: str) -> bool:
    parts = value.split()
    return bool(parts) and all(part in _ZERO for part in parts)


class Source:
    """One file: its comments blanked out in `plain`, and in `code` the inside of its strings too; offsets and lines
    stay where they are, so a span found in one is read in the other."""

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        extension = os.path.splitext(path)[1].lower()
        self.line_starts = [0] + [found.end() for found in re.finditer("\n", text)]
        comments: list[tuple[int, int]] = []
        strings: list[tuple[int, int]] = []
        self.scripts: list[tuple[int, int]] = []
        self.styles: list[tuple[int, int]] = []
        if extension in SCRIPTS:
            self.scripts = [(0, len(text))]
        elif extension in STYLES:
            self.styles = [(0, len(text))]
        elif extension in MARKUPS:
            for tag, ranges in (("script", self.scripts), ("style", self.styles)):
                for found in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", text, re.S | re.I):
                    ranges.append((found.start(1), found.end(1)))
            inside = self.scripts + self.styles
            for found in re.finditer(r"<!--.*?(?:-->|\Z)", text, re.S):
                if not any(a <= found.start() < b for a, b in inside):
                    comments.append((found.start(), found.end()))
        for a, b in self.scripts:
            _lex_script(text, a, b, comments, strings)
        for a, b in self.styles:
            _lex_style(text, a, b, comments)
        plain = list(text)
        _blank(plain, comments, " ")
        self.plain = "".join(plain)
        code = [c if c == "\n" else " " for c in self.plain]
        for a, b in self.scripts:
            code[a:b] = self.plain[a:b]
        _blank(code, strings, "_")
        self.code = "".join(code)
        # Outside its <style> parts, a markup file is read for its inline styles, its Vue bindings and its markup.
        self.loose = [(0, len(text))] if extension in MARKUPS else self.scripts
        self.vue = extension == ".vue"
        self.portal = bool(PORTAL.search("/" + path.replace(os.sep, "/")))
        self.markers: dict[int, Marker] = {}
        for number, line in enumerate(text.splitlines(), 1):
            marker = _marker(line) if OK_MARK in line else None
            if marker:
                self.markers[number] = marker
        self._windows: list[Window] | None = None

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def offset_of_line(self, line: int) -> int:
        return self.line_starts[line - 1] if line <= len(self.line_starts) else len(self.text)

    def in_loose(self, offset: int) -> bool:
        return any(a <= offset < b for a, b in self.loose) and not any(a <= offset < b for a, b in self.styles)

    def in_script(self, offset: int) -> bool:
        return any(a <= offset < b for a, b in self.scripts)

    # ------------------------------------------------------------------ values

    def literal(self, a: int, b: int) -> str | None:
        """The text of a string, bare or in `__()`, written between a and b; None when it is anything else."""
        code = self.code[a:b]
        lead = len(code) - len(code.lstrip())
        stripped = code.strip()
        found = _STRING.match(stripped)
        if not found:
            return None
        if found.group(1):
            close = _matching(self.code, a + lead + found.group(1).index("("))
            if close >= b or self.code[close + 1 : b].strip():
                return None
        elif found.end() != len(stripped):
            return None
        start = a + lead + found.start(3)
        return self.text[start : start + len(found.group(3))]

    def literals_in(self, a: int, b: int) -> list[str]:
        """Every string written between a and b: the branches of `wide ? "large" : "small"`."""
        return [
            self.text[a + found.start(2) : a + found.end(2)]
            for found in _ANY_STRING.finditer(self.code[a:b])
            if "\n" not in found.group()
        ]

    def flag(self, a: int, b: int) -> bool | None:
        """`1` / `true` -> True, `0` / `false` -> False, anything else -> None."""
        value = self.text[a:b].strip()
        return True if value in ("1", "true", "!0") else False if value in ("0", "false", "!1") else None

    def entries(self, a: int, b: int) -> tuple[dict[str, tuple[int, int, int]], bool]:
        """{key: (value start, value end, key offset)} of the object literal between its braces a and b, and whether
        it spreads another object."""
        entries: dict[str, tuple[int, int, int]] = {}
        spread = False
        for x, y in _split_top(self.code, a, b):
            part = self.code[x:y]
            if _SPREAD.match(part):
                spread = True
                continue
            found = _KEY.match(part)
            if found:
                if found.group(1):
                    key, offset = found.group(1), x + found.start(1)
                else:
                    key, offset = self.text[x + found.start(3) : x + found.end(3)], x + found.start(2)
                entries[key] = (x + found.end(), y, offset)
                continue
            found = _METHOD.match(part) or _SHORTHAND.match(part)
            if found:
                entries[found.group(1)] = (x + found.start(1), y, x + found.start(1))
        return entries, spread

    # ------------------------------------------------------------------ the fields of a dialog

    def fields_of(self, a: int, b: int, before: int) -> Fields:
        code = self.code[a:b]
        lead = len(code) - len(code.lstrip())
        stripped = code.strip()
        if stripped.startswith("["):
            opening = a + lead
            closing = _matching(self.code, opening)
            fields = self.array_fields(opening, closing)
            if self.code[closing + 1 : b].strip():  # [...].concat(more), [...].filter(...)
                fields.read = False
            return fields
        if re.fullmatch(r"[\w$]+", stripped):
            declared = None
            for declared in re.compile(r"\b(?:const|let|var)\s+" + re.escape(stripped) + r"\s*=\s*\[").finditer(
                self.code, 0, before
            ):
                pass
            if declared:
                opening = declared.end() - 1
                closing = _matching(self.code, opening)
                fields = self.array_fields(opening, closing)
                changed = re.compile(
                    r"(?<![\w$.])" + re.escape(stripped) + r"\s*(?:\.\s*(?:push|unshift|splice|concat)\s*\(|=(?![=>]))"
                )
                if _CHAINED.match(self.code, closing + 1) or changed.search(self.code, closing, before):
                    fields.read = False  # [...].filter(...), fields.push(more): what is shown is not what is written
                return fields
        return Fields(read=False)

    def array_fields(self, opening: int, closing: int) -> Fields:
        fields = Fields()
        columns = 0
        for x, y in _split_top(self.code, opening + 1, closing):
            part = self.code[x:y]
            if not part.strip().startswith("{"):
                fields.read = False  # a spread, a variable, `cond && {...}`, a call
                continue
            left = x + len(part) - len(part.lstrip())
            right = _matching(self.code, left)
            if self.code[right + 1 : y].strip():
                fields.read = False
                continue
            entries, spread = self.entries(left + 1, right)
            fields.read = fields.read and not spread
            if "fieldtype" not in entries:
                fields.read = False
                continue
            start, end, _offset = entries["fieldtype"]
            one = self.literal(start, end)
            kinds = [one] if one is not None else self.literals_in(start, end)
            if not kinds:
                fields.read = False
                continue
            if kinds == ["Column Break"]:
                columns += 1
                fields.columns = max(fields.columns, columns)
                continue
            if kinds == ["Section Break"]:
                columns = 0
                continue
            if all(kind in NOT_VALUES for kind in kinds):
                continue
            if any(kind in NOT_VALUES for kind in kinds):
                fields.read = False  # a field that is an input or not, by a condition
                continue
            hidden = self.flag(*entries["hidden"][:2]) if "hidden" in entries else False
            if hidden is None:
                fields.read = False
                continue
            if hidden:
                continue
            fields.values += 1
            read_only = self.flag(*entries["read_only"][:2]) if "read_only" in entries else False
            if read_only is None:
                fields.read = False
            elif not read_only:
                fields.typed += 1
        return fields

    # ------------------------------------------------------------------ the windows

    def windows(self) -> list[Window]:
        if self._windows is not None:
            return self._windows
        windows = []
        for found in _NEW_WINDOW.finditer(self.code):
            opening = found.end() - 1
            closing = _matching(self.code, opening)
            kind = "Dialog" if found.group(1) == "Dialog" else "MultiSelectDialog"
            window = Window(kind, found.start(), closing, self.line_of(found.start()), self.line_of(closing))
            assigned = _ASSIGNED.search(self.code, max(0, found.start() - 200), found.start())
            if assigned:
                window.var = re.sub(r"\s+", "", assigned.group(1))
            body = self.code[opening + 1 : closing]
            lead = len(body) - len(body.lstrip())
            if body.strip().startswith("{"):
                left = opening + 1 + lead
                right = _matching(self.code, left)
                window.options, window.spread = self.entries(left + 1, right)
                options = window.options
                if "title" in options:
                    start, end, _offset = options["title"]
                    title = self.literal(start, end)
                    window.title = title if title is not None else " ".join(self.text[start:end].split())[:70]
                if "size" in options:
                    start, end, _offset = options["size"]
                    size = self.literal(start, end)
                    window.size = size if size is not None else "?"
                if "fields" in options:
                    start, end, _offset = options["fields"]
                    window.fields = self.fields_of(start, end, found.start())
                else:
                    window.fields = Fields(read=not window.spread)
                if "steps" in options:
                    window.fields.read = False  # a wizard: its fields are in its steps
            windows.append(window)
        for window, after in zip(windows, windows[1:] + [None]):
            limit = self.offset_of_line(window.end_line + FOLLOW_LINES + 1)
            window.follow_end = min(limit, after.start) if after else limit
        self._windows = windows
        return windows

    def calls_on(self, window: Window, method: str) -> list[tuple[int, int]]:
        """(offset of `(`, offset of its `)`) of each `<variable>.<method>(` after the window, while it is its own."""
        if not window.var:
            return []
        name = r"\s*\.\s*".join(re.escape(part) for part in window.var.split("."))
        pattern = re.compile(r"(?<![\w$.])" + name + r"\s*\.\s*" + method + r"\s*\(")
        calls = []
        for found in pattern.finditer(self.code, window.end, window.follow_end):
            opening = found.end() - 1
            calls.append((opening, _matching(self.code, opening)))
        return calls

    def window_findings(self, window: Window) -> list[Finding]:
        if window.kind != "Dialog" or window.options is None:
            return []
        options, fields = window.options, window.fields or Fields(read=False)
        whole = frozenset(range(window.line, window.end_line + 1))
        found = []

        def refuse(rule: str, offset: int, what: str, extra: frozenset[int] = frozenset()) -> None:
            line = self.line_of(offset)
            anchors = frozenset({window.line, line})
            found.append(Finding(self.path, line, rule, what, whole | extra, anchors, window.line, window.title))

        if "size" in options:
            start, end, offset = options["size"]
            written = [window.size] if window.size != "?" else self.literals_in(start, end)
            for size in written:
                if size and size not in SIZES:
                    what = f'size "{size}" is not a size of the model: small, large or extra-large'
                    refuse("size-missing", offset, what)
        undeclared = window.size in (None, "")
        if fields.values > SHORT_ENTRY:
            count = f"{fields.values}" if fields.read else f"at least {fields.values}"
            declare = 'declare size: "large" (L, 800 px: a form) or "extra-large" (XL)'
            if undeclared and not (window.spread and window.size is None) and fields.columns < 2:
                refuse("size-missing", window.start, f"{count} fields and no size: {declare}")
            elif window.size == "small":
                refuse(
                    "size-missing",
                    options["size"][2],
                    f'{count} fields in size "small" (S, 440 px: a question or a message): {declare}',
                )

        message = fields.read and fields.typed == 0

        def vague(label: str | None) -> str:
            if label is None or _norm(label) not in VAGUE:
                return ""
            if _norm(label) == "ok":
                if message or not fields.read:
                    return ""  # a message has its « OK »; a doubt never refuses
                return "« OK » is the action of a message, and this window takes input: name the action by its verb"
            return f"« {label} » names no verb: name the action by what it does (« Créer la tâche », « Archiver »)"

        if "primary_action_label" in options:
            start, end, offset = options["primary_action_label"]
            what = vague(self.literal(start, end))
            if what:
                refuse("vague-verb", offset, what)
        elif (
            "primary_action" in options
            and "action" not in options
            and not window.spread
            and fields.typed > 0
            and not self.labelled_later(window)
        ):
            refuse(
                "vague-verb",
                options["primary_action"][2],
                "primary_action and no primary_action_label: Frappe names it « Submit » (« Valider »); "
                "name the action by its verb",
            )
        for opening, closing in self.calls_on(window, "set_primary_action"):
            arguments = _split_top(self.code, opening + 1, closing)
            what = vague(self.literal(*arguments[0])) if arguments else ""
            if what:
                refuse("vague-verb", opening, what, frozenset({self.line_of(opening)}))
        return found

    def labelled_later(self, window: Window) -> bool:
        """The window's action is named after it is built: `set_primary_action(...)` or `get_primary_btn().text(...)`,
        on its variable anywhere further in the file (a wizard names it from its step function), or on any receiver
        just after it. A doubt never refuses."""
        just_after = self.code[window.end : window.follow_end]
        if re.search(r"get_primary_btn\s*\(\s*\)\s*\.\s*(?:text|html)\s*\(", just_after):
            return True
        if not window.var:
            return False
        name = re.escape(window.var.split(".")[-1])
        named = re.compile(
            r"(?<![\w$])" + name + r"\s*\.\s*(?:set_primary_action|get_primary_btn\s*\(\s*\)\s*\.\s*(?:text|html))\s*\("
        )
        return bool(named.search(self.code, window.end))

    # ------------------------------------------------------------------ veils and widths

    def blocks(self) -> list[Block]:
        """Every CSS rule of the styles, with its own declarations, and every innermost `{...}` of the rest: a style
        object, a Vue binding, a rule written in a template literal."""
        blocks = []
        for a, b in self.styles:
            blocks += self.css_blocks(a, b)
        for a, b in self.loose:
            for found in _INNER_BLOCK.finditer(self.plain, a, b):
                if not self.in_loose(found.start()):
                    continue
                before = self.plain[max(a, found.start() - 200) : found.start()]
                cut = max((m.end() for m in _SELECTOR_CUT.finditer(before)), default=0)
                block = Block(before[cut:].strip(), found.start(), found.end())
                block.declarations = self.declarations(found.start(1), found.group(1))
                blocks.append(block)
        return blocks

    def css_blocks(self, a: int, b: int) -> list[Block]:
        text = re.sub(r"#\{[^{}]*\}", lambda m: " " * len(m.group()), self.plain[a:b])  # SCSS interpolation
        blocks, stack, position = [], [], 0
        for found in re.finditer(r"[{}]", text):
            j = found.start()
            piece = text[position:j]
            if found.group() == "{":
                cut = piece.rfind(";") + 1
                if stack:
                    stack[-1].declarations += self.declarations(a + position, piece[:cut])
                stack.append(Block(piece[cut:].strip(), a + position + cut, 0))
            elif stack:
                block = stack.pop()
                block.declarations += self.declarations(a + position, piece)
                block.end = a + j
                blocks.append(block)
            position = j + 1
        return blocks

    def declarations(self, offset: int, text: str) -> list[tuple[str, str, int]]:
        return [
            (_camel_to_kebab(found.group(2)), _value(found.group(3)), offset + found.start(2))
            for found in _DECLARATION.finditer(text)
        ]

    def aliases(self) -> set[str]:
        """The classes this file adds to a window's `.modal-dialog`: a CSS rule on one of them sizes the window."""
        names = set()
        for found in _NODE_CALL.finditer(self.plain):
            if found.group(1) in ("addClass", "toggleClass") and self.in_script(found.start()):
                opening = found.end() - 1
                arguments = _split_top(self.code, opening + 1, _matching(self.code, opening))
                value = self.literal(*arguments[0]) if arguments else None
                names |= set((value or "").split()) - _SIZE_CLASSES
        return names

    def line_findings(self, aliases: set[str]) -> list[Finding]:
        refusals: dict[tuple[int, str], Finding] = {}

        def refuse(rule: str, offsets: list[int], what: str, anchors: frozenset[int] | None = None) -> None:
            lines = frozenset(self.line_of(offset) for offset in offsets)
            line = self.line_of(offsets[0])
            refusals.setdefault((line, rule), Finding(self.path, line, rule, what, lines, anchors or lines))

        for block in self.blocks():
            if not block.declarations:
                continue
            declared = {name: (value, offset) for name, value, offset in block.declarations}
            veil = [] if _FRAPPE_MODAL.search(block.selector) else self.covers_the_screen(declared)
            widths = sorted(_WIDTHS & declared.keys()) if block.selector else []
            subject = self.window_selector(block.selector, aliases) if widths else ""
            if not veil and not subject:
                continue
            block_lines = frozenset(range(self.line_of(block.start), self.line_of(block.end) + 1))
            if veil:
                who = _shown(block.selector) or "an element"
                refuse(
                    "hand-made-veil",
                    veil,
                    f"{who} is fixed over the whole screen: a window is a frappe.ui.Dialog with its size, "
                    "never a veil made by hand",
                    block_lines,
                )
            for name in widths if subject else []:
                refuse(
                    "own-width",
                    [declared[name][1]],
                    f"{_shown(subject)} gives a window its {name}: declare size instead: small / large / extra-large",
                    block_lines,
                )
        for a, b in self.loose:
            for found in _INLINE_STYLE.finditer(self.plain, a, b):
                body_start = next(found.start(n) for n in (1, 2, 3) if found.group(n) is not None)
                body = found.group(found.lastindex)
                declared = {name: (value, offset) for name, value, offset in self.declarations(body_start, body)}
                veil = self.covers_the_screen(declared)
                if veil:
                    refuse(
                        "hand-made-veil",
                        veil,
                        "an inline style fixes an element over the whole screen: a window is a frappe.ui.Dialog, "
                        "never a veil made by hand",
                    )
        markup_ranges = [(0, len(self.text))] if self.vue else self.scripts
        for a, b in markup_ranges:
            for found in _MODAL_MARKUP.finditer(self.plain, a, b):
                if "modal" in found.group(1).split() and not any(x <= found.start() < y for x, y in self.styles):
                    refuse("hand-made-veil", [found.start()], 'class="modal" builds a Bootstrap modal by hand: '
                           "a window is a frappe.ui.Dialog")
        for a, b in self.scripts:
            for pattern, what in _MODAL_CALLS:
                for found in pattern.finditer(self.plain, a, b):
                    if self.code[found.start()] == self.plain[found.start()]:  # code, not a string
                        refuse("hand-made-veil", [found.start()], f"{what}: a window is a frappe.ui.Dialog")
            for found in _NODE_CALL.finditer(self.plain, a, b):
                if self.code[found.end() - 1] != "(":
                    continue
                opening = found.end() - 1
                arguments = _split_top(self.code, opening + 1, _matching(self.code, opening))
                method = found.group(1)
                if method == "css" and arguments:
                    first = arguments[0]
                    name = self.literal(*first)
                    names = {_camel_to_kebab(name)} if name else set()
                    text = self.code[first[0] : first[1]].strip()
                    if text.startswith("{"):
                        left = first[0] + self.code[first[0] : first[1]].index("{")
                        names = {_camel_to_kebab(key) for key in self.entries(left + 1, _matching(self.code, left))[0]}
                    if names & _WIDTHS:
                        refuse("own-width", [found.start()], "the window's width is set by hand (.modal-dialog "
                               f".css {sorted(names & _WIDTHS)[0]}): declare size instead: small / large / extra-large")
                elif method == "width" and arguments:
                    refuse("own-width", [found.start()], "the window's width is set by hand (.modal-dialog .width()): "
                           "declare size instead: small / large / extra-large")
            for found in _NODE_STYLE.finditer(self.plain, a, b):
                refuse("own-width", [found.start()], "the window's width is set by hand (.modal-dialog .style): "
                       "declare size instead: small / large / extra-large")
            for found in _CLASS_CALL.finditer(self.plain, a, b):
                classes = set(found.group(1).split()) & _SIZE_CLASSES
                if classes and self.code[found.start()] == self.plain[found.start()]:
                    refuse("own-width", [found.start()], f'addClass("{sorted(classes)[0]}") sizes the window by hand: '
                           "declare size instead: small / large / extra-large")
        return list(refusals.values())

    @staticmethod
    def covers_the_screen(declared: dict[str, tuple[str, int]]) -> list[int]:
        """The offsets of the declarations that fix an element over the whole screen, [] when they do not. An element
        that lets every click through (`pointer-events: none`) is a layer, a highlight or an ink, not a veil."""
        if not declared.get("position", ("",))[0].startswith("fixed"):
            return []
        value = {name: declared[name][0] for name in declared}
        if value.get("pointer-events") == "none":
            return []
        offsets = [declared["position"][1]]
        if _all_zero(value.get("inset", "")):
            return offsets + [declared["inset"][1]]
        across = [n for n in ("left", "right") if value.get(n, "") in _ZERO]
        if len(across) < 2:
            across = ["width"] if value.get("width", "") in _FULL_WIDTH else []
        down = [n for n in ("top", "bottom") if value.get(n, "") in _ZERO]
        if len(down) < 2:
            down = ["height"] if value.get("height", "") in _FULL_HEIGHT else []
        if across and down:
            return offsets + [declared[n][1] for n in across + down]
        return []

    @staticmethod
    def window_selector(selector: str, aliases: set[str]) -> str:
        """The part of the selector whose subject (its last compound) is a window's `.modal-dialog`, or a class added
        to it; "" when none is: `.modal-dialog .preview` sizes the preview, not the window."""
        for one in selector.split(","):
            parts = re.split(r"[\s>+~]+", one.strip())
            last = parts[-1] if parts else ""
            if _WINDOW_CLASS.search(last) or any(re.search(r"\." + re.escape(a) + r"(?![\w-])", last) for a in aliases):
                return one
        return ""

    # ------------------------------------------------------------------ exceptions

    def excused(self, finding: Finding) -> bool:
        for line in finding.anchors | {line - 1 for line in finding.anchors}:
            marker = self.markers.get(line)
            if marker and marker.valid and finding.rule in marker.rules:
                return True
        return False

    def marker_findings(self) -> list[Finding]:
        return [
            Finding(
                self.path,
                line,
                MARKER,
                f'a "{OK_MARK}" marker names its rule ({", ".join(RULES)}) and gives a reason of at least '
                f"{MIN_REASON} characters",
                frozenset({line}),
                frozenset(),
            )
            for line, marker in sorted(self.markers.items())
            if not marker.valid
        ]

    def findings(self, aliases: set[str] = frozenset()) -> list[Finding]:
        """Everything this file breaks, excused or not."""
        found = []
        for window in self.windows():
            found += self.window_findings(window)
        return found if self.portal else found + self.line_findings(set(aliases) | self.aliases())


# ---------------------------------------------------------------------------- what a change adds


def is_judged(path: str) -> bool:
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    if extension not in SCRIPTS | MARKUPS | STYLES or MODEL_FILE.match(name):
        return False
    path = path.replace(os.sep, "/")
    if path.startswith(SKIP_TOP) or BUILT.search("/" + path):
        return False
    return not any(part in "/" + path for part in SKIP_PARTS)


def is_bundled(text: str) -> bool:
    lines = text.count("\n") + 1
    return len(text) > 2000 and len(text) / lines > BUNDLED_LINE


def changed_lines(text: str, base_text: str | None) -> tuple[set[int], set[int]] | None:
    """(added, touched) lines of `text` against `base_text`: added are new or changed, touched are those and the two
    beside a removal. None when the file is new: every line is. A change of indentation is no change."""
    if base_text is None:
        return None
    new = [" ".join(line.split()) for line in text.splitlines()]
    old = [" ".join(line.split()) for line in base_text.splitlines()]
    head = 0  # a change is local: the common head and tail first, difflib is slow on a large file
    while head < len(new) and head < len(old) and new[head] == old[head]:
        head += 1
    tail = 0
    while tail < len(new) - head and tail < len(old) - head and new[-1 - tail] == old[-1 - tail]:
        tail += 1
    matcher = difflib.SequenceMatcher(None, old[head : len(old) - tail], new[head : len(new) - tail])
    added: set[int] = set()
    touched: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        lines = range(head + j1 + 1, head + j2 + 1)
        added.update(lines)
        touched.update(lines)
        if j1 == j2:
            touched.update({head + j1, head + j1 + 1})
    return added, touched


def new_findings(path: str, text: str, base_text: str | None, aliases: set[str] = frozenset()) -> list[Finding]:
    """What this version of a file adds and the rules refuse; `base_text` is the file before, None when it is new."""
    if not is_judged(path) or is_bundled(text):
        return []
    source = Source(path, text)
    changed = changed_lines(text, base_text)
    added, touched = changed if changed else (None, None)
    refused = []
    for finding in source.findings(aliases):
        pool = touched if finding.rule in DIALOG_RULES else added
        if pool is not None and not finding.lines & pool:
            continue  # the stock is rewritten when someone touches it
        if not source.excused(finding):
            refused.append(finding)
    refused += [f for f in source.marker_findings() if added is None or f.line in added]
    return sorted(refused, key=lambda f: (f.line, f.rule))


# ---------------------------------------------------------------------------- the stock


def source_files(root: str):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))  # a worktree is no code
        for name in sorted(files):
            path = os.path.relpath(os.path.join(folder, name), root)
            if is_judged(path):
                yield path


def inventory(root: str) -> list[dict]:
    sources = []
    for path in source_files(root):
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        if not is_bundled(text):
            sources.append(Source(path, text))
    aliases = set().union(*(source.aliases() for source in sources)) if sources else set()
    rows = []
    for source in sources:
        windows = source.windows()
        findings = source.findings(aliases)
        lines = source.text.splitlines()
        for finding in findings:  # a width set on a window's variable, just after it, is that window's
            if finding.window:
                continue
            for window in windows:
                in_reach = window.line <= finding.line and source.offset_of_line(finding.line) < window.follow_end
                named = window.var and re.search(
                    r"(?<![\w$])" + re.escape(window.var.split(".")[-1]) + r"(?![\w$])", lines[finding.line - 1]
                )
                if in_reach and named:
                    finding.window, finding.title = window.line, window.title
                    break
        for window in windows:
            mine = [f for f in findings if f.window == window.line]
            fields = window.fields
            rows.append(row(source, window.line, window.kind, window.title, mine, window, fields))
        for finding in findings + source.marker_findings():
            if not finding.window:
                rows.append(row(source, finding.line, "veil" if finding.rule == "hand-made-veil" else
                                "marker" if finding.rule == MARKER else "width", "", [finding]))
    return rows


def row(source: Source, line: int, kind: str, title: str, findings: list[Finding], window: Window | None = None,
        fields: Fields | None = None) -> dict:
    refused = [f for f in findings if not source.excused(f)]
    return {
        "file": source.path,
        "line": line,
        "kind": kind,
        "title": title,
        "size": window.size if window else None,
        "fields": (fields.values if fields.read else f">={fields.values}") if fields else None,
        "variable": window.var if window else "",
        "rules": sorted({f.rule for f in refused}),
        "detail": "; ".join(f"line {f.line}: {f.what}" for f in refused),
        "excused": sorted({f.rule for f in findings if source.excused(f)}),
    }


def print_inventory(rows: list[dict]) -> None:
    windows = [r for r in rows if r["kind"] in ("Dialog", "MultiSelectDialog")]
    print(f"{len(windows):6d} windows ({sum(r['kind'] == 'Dialog' for r in windows)} frappe.ui.Dialog)")
    print(f"{sum(bool(r['rules']) for r in windows):6d} windows that break a rule")
    counts = Counter(rule for r in rows for rule in r["rules"])
    for rule in (*RULES, MARKER):
        print(f"{counts[rule]:6d} {rule}")
    print(f"{sum(bool(r['excused']) for r in rows):6d} with a reason")


# ---------------------------------------------------------------------------- git


def git(root: str, *arguments: str) -> str | None:
    done = subprocess.run(["git", "-C", root, *arguments], capture_output=True, text=True, errors="replace")
    return done.stdout if done.returncode == 0 else None


def holds_a_merge(root: str, base: str) -> bool:
    """A merge of upstream brings their windows: they are not ours to judge, and on a fork the check would turn every
    merge red. On our single branch a merge is never anything else."""
    return bool((git(root, "rev-list", "--merges", f"{base}..HEAD") or "").strip())


def changed_files(root: str, base: str, staged: bool, on_disk: bool) -> dict[str, str]:
    """{path now: path at the base} of every file of the change: a renamed file is judged against what it was."""
    arguments = ["diff", "--name-status", "-M"]
    arguments += ["--cached", base] if staged else [base] if on_disk else [base, "HEAD"]
    files = {}
    for line in (git(root, *arguments) or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0].startswith("D"):
            continue
        now, before = (parts[2], parts[1]) if parts[0][:1] in "RC" and len(parts) > 2 else (parts[1], parts[1])
        files[now] = before
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="files to read from disk, against --base (default HEAD)")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--base", help="the revision a window is new against")
    parser.add_argument("--staged", action="store_true", help="judge the staged files against HEAD")
    parser.add_argument("--inventory", action="store_true", help="say what every window breaks, refuse nothing")
    parser.add_argument("--json", action="store_true", help="with --inventory: one JSON document")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if args.inventory:
        rows = inventory(root)
        if args.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
        else:
            print_inventory(rows)
        return 0

    base = args.base or ("HEAD" if args.staged or args.files else None)
    if not base:
        parser.error("say what a window is new against: --base <revision>, --staged, or files")

    on_disk = bool(args.files)
    if not on_disk and not args.staged and holds_a_merge(root, base):
        print("dialogs: a merge in the range, upstream's windows are not judged")
        return 0

    renamed = changed_files(root, base, args.staged, on_disk)
    if on_disk:
        paths = {}
        for path in args.files:
            relative = os.path.relpath(os.path.abspath(path), root)
            paths[relative] = renamed.get(relative, relative)
    else:
        paths = renamed
    paths = {now: before for now, before in paths.items() if is_judged(now)}

    def read_now(path: str) -> str | None:
        if on_disk:
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
                    return handle.read()
            except OSError:
                return None
        return git(root, "show", f":{path}" if args.staged else f"HEAD:{path}")

    texts = {path: text for path in paths if (text := read_now(path)) is not None}
    # A class added to a window's .modal-dialog in one file and sized in another: both halves are in this change.
    aliases = set().union(*(Source(path, text).aliases() for path, text in texts.items())) if texts else set()
    findings: list[Finding] = []
    for path, text in texts.items():
        findings += new_findings(path, text, git(root, "show", f"{base}:{paths[path]}"), aliases)

    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    for finding in findings:
        text = f"{finding.rule}: " + (f"{finding.title}: " if finding.title else "") + finding.what
        where = f"file={finding.path},line={finding.line}" if on_github else f"{finding.path}:{finding.line}"
        print(f"::error {where}::{text}" if on_github else f"{where}: {text}")
    if findings:
        print(
            f"\n{len(findings)} refused: a window of the desk is a frappe.ui.Dialog with its size and its verb "
            "(neoffice-design-system, readme §6.18), or says why not on its line: "
            f"`// {OK_MARK} <rule> <reason>`. Why: scripts/check_dialogs.py of bvisible/neoffice-ci.",
            file=sys.stderr,
        )
        return 1
    print("dialogs: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
