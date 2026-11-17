#!/usr/bin/env python3
"""No list of everyday words decides where the assistant decides: found without running anything.

An app whose assistant reads documents and proposes what to do with them (an account, a tax rate, a kind of document)
keeps that decision in one place: the assistant, taught by its doctrine, its instructions, the rules it learnt and the
company's memory. Code that decides in its place with a list of words (`("crate", "pallet", "deposit")` looked for in
the lines of an invoice, then the first account whose name says « packaging ») is always one word late, and every
miss is "fixed" by one more word. On 17.11.2026 such a module was written, pushed and removed the same day; the stock
it had copied from held a dozen lists of the same kind.

A repository opts in with a `.decision-paths.json` at its root (without the file this check does nothing):

    {"watched":  [{"path": "app/api/reading*.py", "why": "the reading of a scanned document"}, ...],
     "declared": [{"path": "app/api/reading.py", "name": "TOTAL_WORDS", "kind": "anchor",
                   "why": "finds the line of the total printed on a receipt",
                   "members": ["amount due", "rechnungsbetrag", "importo totale"],
                   "accepted_by": "the owner", "accepted_on": "2026-11-18"},
                  {"path": "app/api/booking.py", "name": "ACCOUNT_OF", "kind": "decides",
                   "why": "chooses the account of an invoice by the words of its lines",
                   "members": ["waste", "ordures", "6000"],
                   "inherited_on": "2026-11-17", "until": "#312"}]}

`watched` are the paths where the assistant decides (shell patterns on the path from the root, `*` crosses `/`), each
with the reason it is watched. In every `.py` and `.js` file they name, tests left out, the check finds:

  word-collection  a tuple, list or set holding at least MIN_WORDS everyday words
  word-table       a mapping whose keys are at least MIN_WORDS everyday words
  word-pattern     a pattern with alternatives (`crate|pallet|deposit`) holding at least MIN_WORDS everyday words
  word-test        one everyday word looked for in a text: `"vat" in label`, `.startswith("tax")`, `.includes("x")`

An everyday word is made of letters (spaces, hyphens and apostrophes allowed), in lower case or accented, and is not
part of the code's own vocabulary: every name the repository's code uses (variables, functions, attributes, split on
`_` and on capitals) is technical, and so are a few format words. The code is written in English, so a French, German
or Italian word is never a name. What is found is grouped by file and by the name it is assigned to (or the function
it sits in): that group is one list. A text of several lines is prose (a prompt, a message), never a pattern.

It judges the stock, not only the change: an old list is the model the next one is copied from. Every list is either
gone or declared, and a declaration holds the `members` of the list as they were on that day (every string of the
literals that make it, whatever their kind), what the list does (`kind`), and why it is there (`why`):

  decides    it chooses in the assistant's place
  anchor     it finds a figure printed on the document (the total, a rate, a term); it chooses nothing
  support    stop words, company forms, noise: it prepares a comparison, it chooses nothing
  not-words  the check took something else for a list (field names, keys of a format)

A declaration is either accepted (`accepted_by`, `accepted_on`; a list that `decides` also names in `until` the
issue that removes it), or inherited (`inherited_on`, and `until`, the issue that follows it): the stock found the
day a repository opted in, which nobody accepted and which is to be replaced. Either way the list is frozen.

Refused, each with its line: a list that is not declared; a declared list that grew (an everyday word that is not
among its declared members: one more word is a new decision, to be accepted); a declaration whose file or name no
longer exists; a declaration with an unknown `kind`, no reason of MIN_REASON characters, no members, or neither an
acceptance nor an inheritance; a watched pattern with no `why`. A declared list may lose words freely.

What it cannot see is never refused: words read from a data file or a database, built while running, or written only
in English with words the code also uses as names; a single foreign word that some legacy name of the repository
happens to spell. A rule written in the assistant's own instructions is not a list in code: that is where it belongs.
The verdict on a declared list does not depend on the names the rest of the repository uses: a word that becomes or
stops being technical elsewhere never makes a declared list grow.

    check_word_lists.py [--root DIR] [file ...]   judge the repository; with files (pre-commit), only when one of
                                                  them is watched or is the configuration itself
    check_word_lists.py --inventory [--json]      every list of the watched paths, declared or not; refuses nothing
    check_word_lists.py --declare                 the declarations still to write, as JSON to complete and paste

Exit 1 when something is refused.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
import unicodedata
from pathlib import Path

CONFIG = ".decision-paths.json"
MIN_WORDS = 3
MIN_REASON = 20
KINDS = ("decides", "anchor", "support", "not-words")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
SKIP_DIRS = {"node_modules", "dist", "build", "locale", "translations", "__pycache__", "vendor", ".git"}
WORD = re.compile(r"[^\W\d_]+(?:[ '’\-.][^\W\d_]+)*\.?")
PARTS = re.compile(r"[ '’\-.]+")
STOP = {
    "utf", "json", "html", "true", "false", "none", "null", "asc", "desc", "and", "the", "for", "not", "yes", "all",
    "any", "get", "post", "put", "delete", "http", "https", "www", "com", "text", "name", "date", "time", "data",
    "type", "value", "error", "warning", "info", "debug", "left", "right", "center", "top", "bottom",
    "jpg", "jpeg", "jpe", "png", "gif", "webp", "svg", "pdf", "csv", "xml", "xls", "xlsx", "txt", "tiff", "bmp",
}  # fmt: skip
PY_LOOKS_FOR = {"startswith", "endswith", "find", "rfind", "index", "count", "__contains__"}
JS_LOOKS_FOR = ("includes", "indexOf", "startsWith", "endsWith", "search", "match")
JS_STRING = r"""(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)"""
JS_COMMENT_OR_STRING = re.compile(r"(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<string>" + JS_STRING + r")", re.S)
JS_NAME = re.compile(r"[A-Za-z_$][\w$]*")
# A pattern written between slashes, after what can only be followed by a value (never a division).
JS_PATTERN = re.compile(r"(?<=[=(,:!&|?{;\n\[])\s*/((?:[^/\\\n\[]|\\.|\[(?:[^\]\\\n]|\\.)*\])+)/[dgimsuy]*")
MODULE, FILE = "<module>", "<file>"


def is_test(relative: Path) -> bool:
    return relative.name.startswith("test_") or ".test." in relative.name or "tests" in relative.parts


def accented(text: str) -> bool:
    return any(unicodedata.category(char) == "Mn" for char in unicodedata.normalize("NFD", text))


def everyday(text: str, vocabulary: set[str], any_case: bool = False) -> bool:
    """Whether a string is an everyday word: letters, lower case or accented, and not the code's own vocabulary."""
    text = text.strip()
    if not 3 <= len(text) <= 40 or not WORD.fullmatch(text):
        return False
    if not any_case and text != text.lower() and not accented(text):
        return False
    return not all(len(part) < 3 or part in vocabulary or part in STOP for part in PARTS.split(text.lower()) if part)


def same(word: str) -> str:
    return word.strip().lower()


def split_name(name: str) -> set[str]:
    return {name.lower(), *(part for part in re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower().split("_") if part)}


def pattern_runs(text: str) -> list[str]:
    """The runs of letters of a pattern with alternatives; [] for a text that is not one, or that is prose."""
    if "\n" in text or not re.search(r"[\w)\]?*+]\|[\w(\[\\]", text):
        return []
    cleaned = re.sub(r"\(\?[a-zA-Z:=!<]*", "(", re.sub(r"\\[bBdDsSwWAZnrt]", " ", text))
    return [run.replace("\\", "") for run in re.findall(r"[^\W\d_]{3,}(?:\\?[ '’\-][^\W\d_]{3,})*", cleaned)]


# ------------------------------------------------------------------------------------------------------- Python


def python_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        for field in ("id", "attr", "arg", "name"):
            value = getattr(node, field, None)
            if isinstance(value, str):
                names |= split_name(value)
    return names


def python_scopes(tree: ast.AST) -> set[str]:
    """The names a finding can be grouped under in this file: what is assigned, and the functions."""
    scopes = {MODULE}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.add(node.name + "()")
        elif isinstance(node, ast.Assign):
            scopes |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            scopes.add(node.target.id)
    return scopes


def python_findings(tree: ast.AST, vocabulary: set[str]) -> list[tuple[int, str, str, list[str], list[str]]]:
    """(line, kind, name, everyday words, every string of the literal) for every finding of a Python source."""
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    docstrings = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }

    def name_of(node: ast.AST) -> str:
        while node in parent:
            node = parent[node]
            if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
                return node.targets[0].id
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                return node.target.id
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name + "()"
        return MODULE

    def strings_of(nodes) -> list[str]:
        return [node.value for node in nodes if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    def words_of(strings, any_case: bool = False) -> list[str]:
        return [text for text in strings if everyday(text, vocabulary, any_case)]

    found = []

    def keep(node: ast.AST, kind: str, strings: list[str], least: int, any_case: bool = False) -> None:
        words = words_of(strings, any_case)
        if len(set(words)) >= least:
            found.append((node.lineno, kind, name_of(node), words, strings))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            keep(node, "word-collection", strings_of(node.elts), MIN_WORDS)
        elif isinstance(node, ast.Dict):
            keep(node, "word-table", strings_of(key for key in node.keys if key is not None), MIN_WORDS)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings:
            keep(node, "word-pattern", pattern_runs(node.value), MIN_WORDS, any_case=True)
        elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            in_a_literal = isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Constant))
            keep(node, "word-test", [] if in_a_literal else strings_of([node.left]), 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PY_LOOKS_FOR:
            first = node.args[:1]
            strings = strings_of(first[0].elts if first and isinstance(first[0], (ast.Tuple, ast.List)) else first)
            if len(set(words_of(strings))) < MIN_WORDS:  # three or more are a word-collection already
                keep(node, "word-test", strings, 1)
    return found


# --------------------------------------------------------------------------------------------------- JavaScript


def js_code(source: str) -> str:
    """The source with its comments blanked (lines kept), its strings untouched."""
    return JS_COMMENT_OR_STRING.sub(
        lambda found: re.sub(r"[^\n]", " ", found.group("comment")) if found.group("comment") else found.group(0),
        source,
    )


def js_identifiers(code: str) -> set[str]:
    # the words of a string or of a pattern are not names: left in, they would hide themselves from the check
    return set(JS_NAME.findall(JS_PATTERN.sub(" ", re.sub(JS_STRING, '""', code))))


def js_names(code: str) -> set[str]:
    names: set[str] = set()
    for name in js_identifiers(code):
        names |= split_name(name)
    return names


def js_findings(code: str, vocabulary: set[str]) -> list[tuple[int, str, str, list[str], list[str]]]:
    def line_of(position: int) -> int:
        return code.count("\n", 0, position) + 1

    def name_at(position: int) -> str:
        names = re.findall(r"([A-Za-z_$][\w$]*)\s*[:=]\s*(?:new\s+\w+\()?\s*$", code[max(0, position - 160) : position])
        return names[-1] if names else FILE

    def unquote(token: str) -> str:
        return token[1:-1].replace("\\'", "'").replace('\\"', '"')

    found: list[tuple[int, str, str, list[str], list[str]]] = []

    def keep(position: int, kind: str, strings: list[str], least: int, any_case: bool = False) -> None:
        words = [text for text in strings if everyday(text, vocabulary, any_case)]
        if len(set(words)) >= least:
            found.append((line_of(position), kind, name_at(position), words, strings))

    for match in re.finditer(r"\[((?:\s*" + JS_STRING + r"\s*,?)+)\s*\]", code):
        keep(match.start(), "word-collection", [unquote(t) for t in re.findall(JS_STRING, match.group(1))], MIN_WORDS)
    for match in re.finditer(r"\{((?:\s*" + JS_STRING + r"\s*:[^{}]*?,?)+)\s*\}", code):
        keys = [unquote(key) for key in re.findall(r"(" + JS_STRING + r")\s*:", match.group(1))]
        keep(match.start(), "word-table", keys, MIN_WORDS)
    for match in JS_PATTERN.finditer(code):
        keep(match.start(), "word-pattern", pattern_runs(match.group(1)), MIN_WORDS, any_case=True)
    for match in re.finditer(r"RegExp\(\s*(" + JS_STRING + r")", code):
        keep(match.start(), "word-pattern", pattern_runs(unquote(match.group(1))), MIN_WORDS, any_case=True)
    for match in re.finditer(r"\.(?:" + "|".join(JS_LOOKS_FOR) + r")\(\s*(" + JS_STRING + r")\s*[,)]", code):
        keep(match.start(), "word-test", [unquote(match.group(1))], 1)
    return found


# ------------------------------------------------------------------------------------------------- the repository


def sources(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.suffix in (".py", ".js") and path.is_file() and not SKIP_DIRS & set(relative.parts):
            if not path.name.endswith((".min.js", ".bundle.js")):
                yield relative


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def parsed(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def vocabulary_of(root: Path) -> set[str]:
    """Every name the repository's code uses: the words that are technical here."""
    names: set[str] = set()
    for relative in sources(root):
        source = read(root / relative)
        if relative.suffix == ".py":
            tree = parsed(source)
            names |= python_names(tree) if tree else set()
        else:
            names |= js_names(js_code(source))
    return names


def is_watched(config: dict, relative: str) -> bool:
    return any(fnmatch.fnmatchcase(relative, str(entry.get("path") or "")) for entry in config.get("watched") or [])


def lists_of(root: Path, config: dict) -> tuple[list[dict], dict[str, set[str]]]:
    """Every list of the watched paths (one per file and name), and the names each watched file holds."""
    vocabulary = None
    grouped: dict[tuple[str, str], dict] = {}
    scopes: dict[str, set[str]] = {}
    for relative in sources(root):
        if is_test(relative) or not is_watched(config, relative.as_posix()):
            continue
        if vocabulary is None:
            vocabulary = vocabulary_of(root)
        source = read(root / relative)
        if relative.suffix == ".py":
            tree = parsed(source)
            scopes[relative.as_posix()] = python_scopes(tree) if tree else set()
            findings = python_findings(tree, vocabulary) if tree else []
        else:
            code = js_code(source)
            scopes[relative.as_posix()] = js_identifiers(code) | {FILE}
            findings = js_findings(code, vocabulary)
        for line, kind, name, words, members in findings:
            entry = grouped.setdefault(
                (relative.as_posix(), name),
                {"path": relative.as_posix(), "name": name, "line": line, "kinds": [], "words": [], "members": []},
            )
            entry["line"] = min(entry["line"], line)
            entry["kinds"] = sorted({*entry["kinds"], kind})
            for field, values in (("words", words), ("members", members)):
                for value in values:
                    if value.strip() and value.strip() not in entry[field]:
                        entry[field].append(value.strip())
    return sorted(grouped.values(), key=lambda entry: (entry["path"], entry["line"])), scopes


def key_of(entry: dict) -> tuple[str, str]:
    return str(entry.get("path")), str(entry.get("name"))


def standing(declaration: dict | None) -> str:
    """accepted, inherited, or '' for a declaration that says neither (or for no declaration)."""
    if not declaration:
        return ""
    if declaration.get("inherited_on"):
        return "inherited"
    return "accepted" if declaration.get("accepted_by") else ""


def grown(entry: dict, declaration: dict) -> list[str]:
    """The everyday words of a list that are not among the members its declaration holds."""
    members = {same(str(member)) for member in declaration.get("members") or []}
    return [word for word in entry["words"] if same(word) not in members]


def flaws_of(declaration: dict) -> list[str]:
    problems = []
    if declaration.get("kind") not in KINDS:
        problems.append(f"`kind` is one of {', '.join(KINDS)}")
    if len(str(declaration.get("why") or "").strip()) < MIN_REASON:
        problems.append(f"`why` says in at least {MIN_REASON} characters what the list is there for")
    members = declaration.get("members")
    if not isinstance(members, list) or not members or not all(isinstance(member, str) for member in members):
        problems.append("`members` holds the words of the list as they were declared")
    if declaration.get("inherited_on"):
        if not DAY.fullmatch(str(declaration.get("inherited_on"))):
            problems.append("`inherited_on` is the day the list was found (YYYY-MM-DD)")
        if not str(declaration.get("until") or "").strip():
            problems.append("an inherited list names in `until` the issue that follows it")
        if declaration.get("accepted_by"):
            problems.append("a list is inherited or accepted, not both")
    else:
        if not str(declaration.get("accepted_by") or "").strip():
            problems.append("`accepted_by` names who accepted it (or `inherited_on` says the day it was found)")
        if not DAY.fullmatch(str(declaration.get("accepted_on") or "")):
            problems.append("`accepted_on` is the day it was accepted (YYYY-MM-DD)")
        if declaration.get("kind") == "decides" and not str(declaration.get("until") or "").strip():
            problems.append("a list that decides names in `until` the issue that removes it")
    return problems


def judge(found: list[dict], scopes: dict[str, set[str]], config: dict) -> list[str]:
    refused = []
    for entry in config.get("watched") or []:
        if not str(entry.get("why") or "").strip():
            refused.append(f"{CONFIG}: the watched pattern « {entry.get('path')} » does not say why it is watched")
    declared = {key_of(entry): entry for entry in config.get("declared") or []}
    for entry in found:
        where = f"{entry['path']}:{entry['line']}: {entry['name']}"
        declaration = declared.get(key_of(entry))
        if declaration is None:
            sample = ", ".join(entry["words"][:6]) + ("…" if len(entry["words"]) > 6 else "")
            refused.append(
                f"{where}: a list of everyday words where the assistant decides ({len(entry['words'])}: {sample}). "
                f"Give the decision back to the assistant, or declare the list in {CONFIG} (--declare prints it)."
            )
            continue
        more = grown(entry, declaration)
        if more:
            refused.append(
                f"{where}: the declared list grew by {len(more)} word(s): {', '.join(more[:8])}. One more word is a "
                f"new decision: give it back to the assistant, or have the list accepted again in {CONFIG}."
            )
    for key, declaration in declared.items():
        if key[1] not in scopes.get(key[0], ()):
            refused.append(f"{CONFIG}: {key[0]} {key[1]} is declared but this file or this name no longer exists")
        problems = flaws_of(declaration)
        if problems:
            refused.append(f"{CONFIG}: the declaration of {key[0]} {key[1]} is incomplete: " + "; ".join(problems))
    return refused


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="pre-commit: judge only when one of these is watched")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--inventory", action="store_true", help="list every list, refuse nothing")
    parser.add_argument("--json", action="store_true", help="with --inventory: one JSON document")
    parser.add_argument("--declare", action="store_true", help="print the declarations still to write")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root / CONFIG).is_file():
        print(f"no {CONFIG} in this repository: nothing is watched")
        return 0
    try:
        config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"{CONFIG} is not valid JSON: {error}")
        return 1
    if args.files and not any(name == CONFIG or is_watched(config, Path(name).as_posix()) for name in args.files):
        return 0
    found, scopes = lists_of(root, config)
    declared = {key_of(entry): entry for entry in config.get("declared") or []}
    if args.declare:
        todo = [
            {
                "path": entry["path"],
                "name": entry["name"],
                "kind": "",
                "why": "",
                "members": entry["members"],
                "accepted_by": "",
                "accepted_on": "",
            }
            for entry in found
            if key_of(entry) not in declared or grown(entry, declared[key_of(entry)])
        ]
        print(json.dumps(todo, indent=2, ensure_ascii=False))
        return 0
    if args.inventory:
        for entry in found:
            declaration = declared.get(key_of(entry))
            entry["standing"] = standing(declaration) or ("declared" if declaration else "")
            entry["declared_kind"] = (declaration or {}).get("kind") or ""
        if args.json:
            print(json.dumps(found, indent=1, ensure_ascii=False))
        else:
            for entry in found:
                state = f"{entry['standing']} {entry['declared_kind']}".strip() or "NOT DECLARED"
                print(
                    f"{entry['path']}:{entry['line']} {entry['name']} [{state}] "
                    f"{len(entry['words'])}: {', '.join(entry['words'][:8])}"
                )
            counts = {state: sum(entry["standing"] == state for entry in found) for state in ("accepted", "inherited")}
            print(
                f"{len(found)} list(s) in the watched paths: {counts['accepted']} accepted, "
                f"{counts['inherited']} inherited, {sum(not entry['standing'] for entry in found)} not declared"
            )
        return 0
    refused = judge(found, scopes, config)
    for line in refused:
        print(line)
    if refused:
        print(f"\n{len(refused)} refusal(s). The rule and the way to declare a list: the docstring of this check.")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
