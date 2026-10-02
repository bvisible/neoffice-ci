#!/usr/bin/env python3
"""Test modules that break the whole run for everyone, found without running anything.

Two patterns each cost a full CI run on 02.10, and each is invisible in review because the test that writes it
passes where its author runs it:

  1. A module-level `pytest.importorskip("x")` or `pytest.skip(..., allow_module_level=True)`.
     Under pytest it is a clean skip. The Frappe unittest runner (`bench run-tests`, the CI of every app) imports
     every test_*.py too, and there `Skipped` is an uncaught exception that kills the run at collection: not one
     test runs. nora: five files, six hours with no test (02.10). Skip with a mark instead:
     `pytestmark = pytest.mark.skipif(find_spec("x") is None, reason=...)`, and import inside the function.

  2. `patch.object(frappe.local, <name>, <value>, create=True)`.
     frappe.local is a werkzeug Local: its attributes live in a context variable, not in a `__dict__`, so `patch`
     takes an attribute the site had set for a new one, deletes it when the block ends and, because of
     `create=True`, never puts it back. Every test after that one then loses `frappe.flags` (or `site`, or
     `form_dict`): theme, 01.10 with `site` (93 errors) and 02.10 with `flags` (159 errors, the run stopped at
     915 tests of about 2 000). Set the attribute and restore it yourself in a context manager.

    check_test_hygiene.py [--root DIR] [--strict]

The first pattern always kills the run: it is an error (exit 1). The second only does when the attribute is one that
a bound site has (`flags`, `site`, `conf`, `db`, `session`, `form_dict`, `lang`, the logs, `cache`): those are a
warning, an annotation on the run, because several apps still carry the pattern on attributes that only exist in a
request and nothing fails there; `--strict` makes them errors too. A line may carry `# hygiene: ok <reason>` when the
pattern is on purpose. Nothing is imported or executed: the files are parsed.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", ".tox", ".mypy_cache"}
OK_MARK = "hygiene: ok"
# Attributes `frappe.init` / `frappe.connect` set: a patch with create=True on one of them takes it away for good.
BOUND_ATTRIBUTES = {
    "flags", "site", "site_path", "sites_path", "conf", "db", "session", "form_dict", "lang", "cache",
    "message_log", "error_log", "debug_log", "realtime_log",
}


def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` - its body runs when the file is a script, not when a runner imports it."""
    test = getattr(node, "test", None)
    return (
        isinstance(node, ast.If)
        and isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _import_time_nodes(tree: ast.AST):
    """Every node that runs when the module is imported: the module body and class bodies, not function bodies and
    not the `__main__` guard."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if _is_main_guard(child):
                stack.extend(child.orelse)  # the body is a script's, the else branch is an import's
                continue
            stack.append(child)


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def module_level_skips(tree: ast.AST) -> list[int]:
    """Lines of `*.importorskip(...)` and `*.skip(..., allow_module_level=True)` run at import time."""
    found = []
    for node in _import_time_nodes(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "importorskip":
            found.append(node.lineno)
        elif node.func.attr == "skip" and any(k.arg == "allow_module_level" and _is_true(k.value) for k in node.keywords):
            found.append(node.lineno)
    return found


def _is_patch_object(func: ast.AST) -> bool:
    """`patch.object` or `mock.patch.object` (also `unittest.mock.patch.object`)."""
    if not (isinstance(func, ast.Attribute) and func.attr == "object"):
        return False
    base = func.value
    return (isinstance(base, ast.Name) and base.id == "patch") or (isinstance(base, ast.Attribute) and base.attr == "patch")


def _is_frappe_local(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "local"
        and isinstance(node.value, ast.Name)
        and node.value.id == "frappe"
    )


def local_patches_with_create(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, attribute) of `patch.object(frappe.local, "<attribute>", ..., create=True)` on an attribute a bound
    site has, anywhere in the file. A name that is not a string literal cannot be judged and is left alone."""
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _is_patch_object(node.func)
            and len(node.args) >= 2
            and _is_frappe_local(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in BOUND_ATTRIBUTES
            and any(k.arg == "create" and _is_true(k.value) for k in node.keywords)
        ):
            found.append((node.lineno, node.args[1].value))
    return found


def check_source(source: str) -> list[tuple[int, str, str]]:
    """[(line, "error" | "warning", what)] for one file's text; a line marked `# hygiene: ok` is left alone."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # not this script's business: the test run reports it
    lines = source.splitlines()

    def allowed(lineno: int) -> bool:
        return 0 < lineno <= len(lines) and OK_MARK in lines[lineno - 1]

    findings = [
        (n, "error", "module-level pytest skip: it kills the Frappe unittest run at collection (use pytestmark = skipif)")
        for n in module_level_skips(tree)
        if not allowed(n)
    ]
    findings += [
        (
            n,
            "warning",
            f"patch.object(frappe.local, {name!r}, ..., create=True) deletes that attribute for good when the block "
            "ends: every later test loses it (set it and restore it)",
        )
        for n, name in local_patches_with_create(tree)
        if not allowed(n)
    ]
    return sorted(findings)


def test_files(root: str):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.startswith("test_") and name.endswith(".py"):
                yield os.path.join(folder, name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=".", help="the repository to scan (default: the current directory)")
    parser.add_argument("--strict", action="store_true", help="make the warnings errors too")
    args = parser.parse_args()

    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    errors = warnings = 0
    for path in test_files(args.root):
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        rel = os.path.relpath(path, args.root)
        for line, kind, what in check_source(source):
            if kind == "error" or args.strict:
                errors += 1
                label = "error"
            else:
                warnings += 1
                label = "warning"
            # An annotation on the run when it is GitHub that reads it, a plain line otherwise.
            print(f"::{label} file={rel},line={line}::{what}" if on_github else f"{rel}:{line}: {label}: {what}")
    if errors:
        print(f"\n{errors} error(s), {warnings} warning(s). Why: scripts/check_test_hygiene.py of bvisible/neoffice-ci.", file=sys.stderr)
        return 1
    print(f"test hygiene: ok ({warnings} warning(s))" if warnings else "test hygiene: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
