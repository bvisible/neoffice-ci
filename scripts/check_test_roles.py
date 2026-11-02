#!/usr/bin/env python3
"""A new test does not act as Administrator: found without running anything.

Administrator is our account. No client ever creates an order or an invoice with it, and no permission check ever
refuses it, so a test that plays the act as Administrator proves nothing about a real user. A Frappe test runs as
Administrator unless it says otherwise, so saying nothing is enough. neoffice-maintenance#2114 (a customer of the
portal could no longer pay) went through thousands of green tests that way, and its family had already struck three
times (#277, #1959, #2056): an upstream merge adds a right check, the session of a real user is the one that meets it.

The rule, for a NEW test only (an existing one is left alone until someone rewrites it, so nothing turns red at once):
an act that creates or submits a business document (BUSINESS_DOCTYPES) is played by the role that does it in real life.

  - `with as_role("sales_user"):`, `with self.set_user(...)`, `frappe.set_user(<someone real>)`: the act is real.
  - Administrator may prepare the scenery: what it creates in `setUp`, in a fixture, or in a test before it hands over
    to a real user (the order a customer then pays) is not the act.
  - A test that creates such a document and never hands over to anyone is refused.
  - `ignore_permissions=True` on the act of a real user is refused too: the check it skips is the one under test.

A range that holds a merge is not judged: on a fork, a merge of upstream brings hundreds of tests of theirs.

An act is what the test does itself, what a helper of the same file does, and what a function of the app does when
the test calls it: `place_order()` is an act even if the test never names a Sales Order. A test that patches
`frappe.get_doc` and its kind creates no document and is not judged on the functions it calls.

A test may say why it is an exception, on its `def` line (or on the `class` line for all of its tests), or on the
line of the act, with a reason: a scheduled job does run as Administrator in real life.

    def test_the_nightly_job_invoices(self):  # role-check: ok the scheduler runs as Administrator in real life

    check_test_roles.py --base <revision>         every test changed since that revision (CI)
    check_test_roles.py --staged                  what is staged, against HEAD
    check_test_roles.py [--base HEAD] file...     these files as they are on disk (pre-commit)
    check_test_roles.py --inventory [--json]      what every test of the repository does, nothing is refused

Nothing is imported or executed: the files are parsed. Exit 1 when a new act is refused.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field

BUSINESS_DOCTYPES = {
    "Quotation",
    "Sales Order",
    "Sales Invoice",
    "Delivery Note",
    "POS Invoice",
    "Payment Entry",
    "Payment Request",
    "Journal Entry",
    "Purchase Order",
    "Purchase Invoice",
    "Purchase Receipt",
    "Stock Entry",
    "Stock Reconciliation",
}
# The helpers of erpnext's own tests and of ours: `make_sales_invoice`, `create_payment_entry`, `submit_quotation`...
HELPER_DOCTYPES = {doctype.lower().replace(" ", "_"): doctype for doctype in BUSINESS_DOCTYPES}
_HELPER_DOCTYPE = r"(?:\w+_)?(" + "|".join(HELPER_DOCTYPES) + r")$"
HELPER_NAME = re.compile(r"^_?(?:make|create|new|insert|submit)_" + _HELPER_DOCTYPE)
# Gives a document without creating it: `get_payment_entry(...)` is an act only once it is inserted or submitted.
GETTER_NAME = re.compile(r"^_?(?:get|build)_" + _HELPER_DOCTYPE)
SAVE_VERBS = {"insert", "save", "submit"}
DOC_SOURCES = {"get_doc", "new_doc", "get_last_doc", "get_cached_doc"}
ADMIN = "Administrator"
OK_MARK = "role-check: ok"
MIN_REASON = 10  # a reason, not "ok" or "n/a"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", ".tox", ".mypy_cache"}
# A context manager or a helper whose name says it changes who acts.
# A context of a file of its own (`with _as(CASHIER):`) is also recognised by what it does, see Walker.context_user.
ADMIN_CONTEXT = re.compile(r"(^|_)(administrator|admin|superuser)($|_)")
USER_CONTEXT = re.compile(
    r"^_?as$|^_?as_|(^|_)(set_user|run_as|acting_as|logged_in|signed_in|login|login_as|impersonate|with_user|session_of)($|_)"
)
# Patching one of these means the test creates no document: what it calls is not an act.
ENTER_CONTEXT = {"enterContext", "enterClassContext", "enterModuleContext"}  # unittest: `with` for a whole test
MOCKED_DOCUMENT = re.compile(r"(^|\.)frappe(\.(get_doc|new_doc|get_cached_doc))?$")
# How far a call of a test is followed through the app. `place_order()` reaches its Sales Order in one or two; further
# than that a catalogue feed reaches a Quotation because it asks for a price, and the check starts crying wolf.
MAX_HOPS = 2
# Names too common to say anything when they are found in a test: `save()`, `update()`, `run()`...
COMMON_NAMES = {"save", "submit", "insert", "get", "set", "update", "run", "execute", "create", "make", "add", "main", "validate"}


@dataclass
class Act:
    line: int
    doctype: str
    verb: str
    user: str  # "admin", "role", or "caller" inside a helper: whoever calls it
    ignored: bool = False  # ignore_permissions on this act
    via: str = ""  # the helper or the function of the app it went through
    allowed: bool = False  # `# role-check: ok <reason>` on its line
    clock: int = 0  # when it happens in the test, to tell what a real user comes after

    def key(self) -> tuple[str, str, str]:
        return (self.doctype, self.verb, self.via)


@dataclass
class Summary:
    """What a function does when it is run."""

    acts: list[Act] = field(default_factory=list)
    end_user: str | None = None  # who acts after it, None when it changes nothing
    last_role: int = -1  # when a real user last acted, -1 when none ever does
    clock: int = 0  # how long it lasts, for the caller's clock
    changes_user: bool = False  # it sets the user, whether or not it gives it back
    mocked: bool = False  # it patches the creation of documents

    @property
    def saw_role(self) -> bool:
        return self.last_role >= 0


@dataclass
class Finding:
    path: str
    test: str
    line: int
    what: str


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _last_name(func: ast.AST) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value)


def _reason_line(source_line: str) -> bool:
    if OK_MARK not in source_line:
        return False
    return len(source_line.split(OK_MARK, 1)[1].strip()) >= MIN_REASON


def _doctype_of_dict(node: ast.AST) -> str | None:
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "doctype" and isinstance(value, ast.Constant):
                return value.value if isinstance(value.value, str) else None
    return None


class Module:
    """One parsed test file: its constants, its functions, its classes."""

    def __init__(
        self,
        source: str,
        app_functions: dict[str, set[str]] | None = None,
        app_packages: set[str] | None = None,
    ):
        self.tree = ast.parse(source)
        self.lines = source.splitlines()
        self.app_functions = app_functions or {}
        self.imported_functions: dict[str, str] = {}  # local name of a name imported from the app: its real name
        self.imported_modules: set[str] = set()  # local name that may be a module of the app
        self._read_imports(app_packages or set())
        self.constants: dict[str, str] = {}
        self.dicts: dict[str, str] = {}
        self.functions: dict[str, ast.AST] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        self._summaries: dict[tuple[str, str], Summary] = {}
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    self.constants[node.targets[0].id] = node.value.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node

    def _read_imports(self, app_packages: set[str]) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                if node.level or (node.module or "").split(".")[0] in app_packages:
                    for alias in node.names:
                        self.imported_functions[alias.asname or alias.name] = alias.name
                        self.imported_modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in app_packages:
                        self.imported_modules.add(alias.asname or alias.name.split(".")[0])

    def app_function(self, func: ast.AST) -> str | None:
        """The real name of the function of the app this call is, None when it is anything else: `d.items()` is no
        function of the app, whatever the app calls its own."""
        if isinstance(func, ast.Name):
            return self.imported_functions.get(func.id)
        if isinstance(func, ast.Attribute) and _dotted(func.value).split(".")[0] in self.imported_modules:
            return func.attr
        return None

    def line_allows(self, lineno: int) -> bool:
        return 0 < lineno <= len(self.lines) and _reason_line(self.lines[lineno - 1])

    def method(self, class_name: str | None, name: str) -> tuple[ast.AST, str | None] | None:
        """A method of the class or of a base class of this file."""
        seen: set[str] = set()
        queue = [class_name] if class_name else []
        while queue:
            current = queue.pop(0)
            if current in seen or current not in self.classes:
                continue
            seen.add(current)
            for node in self.classes[current].body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return node, current
            queue.extend(_last_name(base) for base in self.classes[current].bases)
        return None

    def summary(self, func: ast.AST, class_name: str | None, start_user: str = "caller") -> Summary:
        key = (class_name or "", func.name + "@" + start_user)
        if key not in self._summaries:
            self._summaries[key] = Summary()  # stops recursion: a function that calls itself adds nothing
            walker = Walker(self, class_name, start_user)
            walker.block(func.body)
            is_context = any(_last_name(d) == "contextmanager" for d in func.decorator_list)
            walker.result.end_user = walker.user if walker.user != start_user and not is_context else None
            walker.result.mocked = walker.result.mocked or _patches_documents(func)
            self._summaries[key] = walker.result
        return self._summaries[key]

    def class_start_user(self, class_name: str) -> str:
        """The user a test of this class starts with, from setUp and setUpClass."""
        user = "admin"
        for name in ("setUpClass", "setUp"):
            found = self.method(class_name, name)
            if found:
                ended = self.summary(found[0], found[1], "admin").end_user
                user = ended or user
        return user

    def tests(self):
        """(class name or None, test function) of every test of the file."""
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                yield None, node
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                        yield node.name, item

    def decorated_user(self, func: ast.AST) -> str | None:
        for decorator in func.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if USER_CONTEXT.search(_last_name(target)):
                return self._user_of(decorator.args[0]) if isinstance(decorator, ast.Call) and decorator.args else "role"
        return None

    def _user_of(self, node: ast.AST) -> str:
        value = node.value if isinstance(node, ast.Constant) else self.constants.get(getattr(node, "id", ""))
        return "admin" if value == ADMIN else "role"


def _patch_target(node: ast.AST) -> str | None:
    """The dotted target of `patch("x.y")`, `patch.object(frappe, "get_doc")`, `@patch(...)`."""
    if not isinstance(node, ast.Call):
        return None
    name = _dotted(node.func)
    if name.endswith("patch") and node.args and isinstance(node.args[0], ast.Constant):
        return str(node.args[0].value)
    if name.endswith("patch.object") and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return f"{_dotted(node.args[0])}.{node.args[1].value}"
    return None


def _patches_documents(func: ast.AST) -> bool:
    calls = list(func.decorator_list)
    calls += [n for n in ast.walk(func) if isinstance(n, ast.Call)]
    return any(MOCKED_DOCUMENT.search(_patch_target(call) or "") for call in calls)


class Walker:
    """Reads a function in order and keeps track of who is acting."""

    def __init__(self, module: Module, class_name: str | None, start_user: str):
        self.module = module
        self.class_name = class_name
        self.user = start_user
        self.variables: dict[str, str] = {}
        self.ignored_variables: set[str] = set()
        self.result = Summary()
        self.result.last_role = 0 if start_user == "role" else -1

    def tick(self) -> int:
        self.result.clock += 1
        return self.result.clock

    def set_user(self, user: str) -> None:
        self.user = user
        self.result.changes_user = True
        clock = self.tick()
        if user == "role":
            self.result.last_role = clock

    def block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.statement(statement)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            self.with_block(node)
        elif isinstance(node, ast.Try):
            self.block(node.body)
            for handler in node.handlers:
                self.block(handler.body)
            self.block(node.orelse)
            self.block(node.finalbody)
        elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            self.calls_in(node.test if isinstance(node, (ast.If, ast.While)) else node.iter)
            self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return  # a local definition does nothing until it is called
        elif isinstance(node, ast.Assign):
            self.assignment(node)
        else:
            self.calls_in(node)

    def with_block(self, node: ast.With) -> None:
        saved = self.user
        for item in node.items:
            call = item.context_expr
            user = self.context_user(call)
            if user:
                self.set_user(user)
            elif MOCKED_DOCUMENT.search(_patch_target(call) or ""):
                self.result.mocked = True
            else:
                self.calls_in(call)
        self.block(node.body)
        if self.user != saved:
            self.set_user(saved)

    def local_function(self, func: ast.AST) -> tuple[ast.AST, str | None] | None:
        """The function of this file a call names."""
        name = _last_name(func)
        owner = _dotted(func.value) if isinstance(func, ast.Attribute) else ""
        if owner in ("self", "cls"):
            return self.module.method(self.class_name, name)
        if not owner and name in self.module.functions:
            return self.module.functions[name], None
        return None

    def context_user(self, call: ast.AST) -> str | None:
        """Who acts inside `with <call>:`, None when it has nothing to do with users. Every file has its own context
        (`_as`, `signed_in`, `as_customer`...): its name may say it, otherwise what it does does."""
        target = call.func if isinstance(call, ast.Call) else call
        name = _last_name(target)
        named = self.module._user_of(call.args[0]) if isinstance(call, ast.Call) and call.args else "role"
        if ADMIN_CONTEXT.search(name):
            return "admin"
        if USER_CONTEXT.search(name):
            return named
        found = self.local_function(target)
        if found:
            summary = self.module.summary(found[0], found[1])
            if summary.changes_user:
                return named if isinstance(call, ast.Call) and call.args else "role" if summary.saw_role else "admin"
        return None

    def assignment(self, node: ast.Assign) -> None:
        self.calls_in(node.value)
        name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else None
        if name:
            doctype = self.doctype_of(node.value)
            if doctype:
                self.variables[name] = doctype
            else:
                self.variables.pop(name, None)
            doctype_of_dict = _doctype_of_dict(node.value)
            if doctype_of_dict:
                self.variables[name + "#dict"] = doctype_of_dict
        target = node.targets[0]
        if _dotted(target).endswith("session.user"):
            self.set_user(self.module._user_of(node.value))
        # doc.flags.ignore_permissions = True
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "ignore_permissions"
            and _is_true(node.value)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
        ):
            self.ignored_variables.add(target.value.value.id)

    def doctype_of(self, node: ast.AST) -> str | None:
        """The business doctype this expression gives a document of, if any."""
        if isinstance(node, ast.Name):
            return self.variables.get(node.id)
        if not isinstance(node, ast.Call):
            return None
        name = _last_name(node.func)
        if name in SAVE_VERBS and isinstance(node.func, ast.Attribute):
            return self.doctype_of(node.func.value)
        if name in DOC_SOURCES and _dotted(node.func).startswith("frappe"):
            first = node.args[0] if node.args else None
            if name == "get_doc" and isinstance(first, ast.Name):
                doctype = self.variables.get(first.id + "#dict")
                return doctype if doctype in BUSINESS_DOCTYPES else None
            doctype = _doctype_of_dict(first) if isinstance(first, ast.Dict) else None
            if doctype is None and isinstance(first, ast.Constant):
                doctype = first.value
            if doctype is None:
                for keyword in node.keywords:
                    if keyword.arg == "doctype" and isinstance(keyword.value, ast.Constant):
                        doctype = keyword.value.value
            return doctype if doctype in BUSINESS_DOCTYPES else None
        match = HELPER_NAME.match(name) or GETTER_NAME.match(name)
        return HELPER_DOCTYPES[match.group(1)] if match else None

    def calls_in(self, node: ast.AST) -> None:
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        for call in sorted(calls, key=lambda c: (c.end_lineno or c.lineno, c.end_col_offset or 0)):
            self.call(call)

    def call(self, node: ast.Call) -> None:
        name = _last_name(node.func)
        dotted = _dotted(node.func)
        if dotted.endswith("frappe.set_user") or dotted == "set_user":
            self.set_user(self.module._user_of(node.args[0]) if node.args else "role")
        elif name in ENTER_CONTEXT and node.args and (user := self.context_user(node.args[0])):
            self.set_user(user)
        elif name in SAVE_VERBS and isinstance(node.func, ast.Attribute):
            self.save_call(node, name)
        elif not self.local_call(node, name):
            self.known_helper(node, name)

    def save_call(self, node: ast.Call, verb: str) -> None:
        receiver = node.func.value
        doctype = self.doctype_of(receiver)
        if doctype:
            ignored = any(k.arg == "ignore_permissions" and _is_true(k.value) for k in node.keywords)
            ignored = ignored or (isinstance(receiver, ast.Name) and receiver.id in self.ignored_variables)
            self.add(node.lineno, doctype, verb, ignored=ignored)

    def add(self, line: int, doctype: str, verb: str, ignored: bool = False, via: str = "") -> None:
        clock = self.tick()
        if self.user == "role":
            self.result.last_role = clock
        self.result.acts.append(Act(line, doctype, verb, self.user, ignored, via, self.module.line_allows(line), clock))

    def local_call(self, node: ast.Call, name: str) -> bool:
        """A function or a method of this file: its acts and the user it leaves behind are the caller's."""
        found = self.local_function(node.func)
        if not found:
            return False
        summary = self.module.summary(found[0], found[1])
        self.result.mocked = self.result.mocked or summary.mocked
        start = self.result.clock
        for act in summary.acts:
            user = self.user if act.user == "caller" else act.user
            allowed = act.allowed or self.module.line_allows(node.lineno)
            clock = start + act.clock
            if user == "role":
                self.result.last_role = max(self.result.last_role, clock)
            self.result.acts.append(
                Act(node.lineno, act.doctype, act.verb, user, act.ignored, act.via or name, allowed, clock)
            )
        self.result.clock = start + summary.clock
        if summary.saw_role:
            self.result.last_role = max(self.result.last_role, start + summary.last_role)
        self.result.changes_user = self.result.changes_user or summary.changes_user
        if summary.end_user:
            self.set_user(summary.end_user)
        return True

    def known_helper(self, node: ast.Call, name: str) -> None:
        match = HELPER_NAME.match(name)
        if match:
            ignored = any(k.arg == "ignore_permissions" and _is_true(k.value) for k in node.keywords)
            self.add(node.lineno, HELPER_DOCTYPES[match.group(1)], "create", ignored=ignored, via=name)
            return
        target = self.module.app_function(node.func)
        if target and target not in COMMON_NAMES:
            for doctype in sorted(self.module.app_functions.get(target, ())):
                self.add(node.lineno, doctype, "calls", via=target)


# ---------------------------------------------------------------------------- the functions of the app


def app_packages_of(root: str) -> set[str]:
    return {
        entry
        for entry in os.listdir(root)
        if os.path.isfile(os.path.join(root, entry, "__init__.py")) and entry not in SKIP_DIRS
    }


def app_functions(root: str, packages: set[str]) -> dict[str, set[str]]:
    """{function name: business doctypes it creates or submits}, through the app's own calls, outside the tests.

    A call counts when it can only be a function of the app: imported from one of its packages, or defined in the same
    file. A name defined twice stays in: a false friend costs one reasoned line in a new test, a missed act costs a
    test that proves nothing.
    """
    direct: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}
    for path in python_files(root):
        if os.path.basename(path).startswith("test_") or "/tests/" in path:
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            try:
                module = Module(handle.read(), app_packages=packages)
            except (SyntaxError, ValueError):
                continue
        here = {n.name for n in ast.walk(module.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walker = Walker(module, None, "caller")
                walker.block(node.body)
                direct.setdefault(node.name, set()).update(act.doctype for act in walker.result.acts)
                for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                    name = _last_name(call.func)
                    local = name in here and (isinstance(call.func, ast.Name) or _dotted(call.func.value) in ("self", "cls"))
                    target = name if local else module.app_function(call.func)
                    if target:
                        calls.setdefault(node.name, set()).add(target)
    for _hop in range(MAX_HOPS):
        reached = {
            name: set().union(*(direct.get(other, set()) for other in called - COMMON_NAMES), set())
            for name, called in calls.items()
        }
        for name, doctypes in reached.items():
            direct[name] |= doctypes
    return {name: doctypes for name, doctypes in direct.items() if doctypes}


def python_files(root: str):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(folder, name)


# ---------------------------------------------------------------------------- what one file says


@dataclass
class TestReport:
    name: str  # Class.test or test
    line: int
    kind: str  # no-act, real, scenery, admin-act, ignored, mocked
    acts: list[Act]
    allowed: bool
    bad: list[Act]


def judge(module: Module, class_name: str | None, func: ast.AST) -> TestReport:
    start = module.decorated_user(func) or (module.class_start_user(class_name) if class_name else "admin")
    summary = module.summary(func, class_name, start)
    name = f"{class_name}.{func.name}" if class_name else func.name
    class_line = module.classes[class_name].lineno if class_name else 0
    allowed = any(module.line_allows(n) for n in (func.lineno, func.lineno - 1, class_line) if n)
    acts = summary.acts
    direct = [a for a in acts if a.verb != "calls"]
    judged = direct if summary.mocked else acts
    # Administrator prepares and a real user then takes over: what comes before a real user is the scenery. An act no
    # real user comes after prepares nothing, and a test where nobody ever takes over has only Administrator acting.
    bad = [
        a
        for a in judged
        if not a.allowed
        and ((a.user == "admin" and a.clock > summary.last_role) or (a.user == "role" and a.ignored))
    ]
    if bad:
        kind = "ignored" if all(a.ignored for a in bad) else "admin-act"
    elif not judged:
        kind = "mocked" if summary.mocked and acts else "no-act"
    elif any(a.user == "role" for a in judged):
        kind = "real"
    else:
        kind = "scenery"
    return TestReport(name, func.lineno, kind, acts, allowed, bad)


def read_module(source: str, functions: dict[str, set[str]], packages: set[str]) -> Module | None:
    try:
        return Module(source, functions, packages)
    except (SyntaxError, ValueError):
        return None  # not this script's business: the test run reports it


def new_findings(
    path: str,
    source: str,
    base_source: str | None,
    functions: dict[str, set[str]] | None = None,
    packages: set[str] | None = None,
) -> list[Finding]:
    """What this version of a file adds and the rule refuses; `base_source` is the file before, None when it is new."""
    functions, packages = functions or {}, packages or set()
    module = read_module(source, functions, packages)
    if module is None:
        return []
    before: dict[str, TestReport] = {}
    if base_source is not None and (old := read_module(base_source, functions, packages)) is not None:
        before = {report.name: report for report in (judge(old, c, f) for c, f in old.tests())}
    findings = []
    for class_name, func in module.tests():
        report = judge(module, class_name, func)
        if report.allowed or not report.bad:
            continue
        known = Counter(a.key() for a in before[report.name].bad) if report.name in before else Counter()
        for act in report.bad:
            if known[act.key()] > 0:
                known[act.key()] -= 1  # it was there: an old test is rewritten when someone chooses to
                continue
            how = f"through {act.via}() " if act.via else ""
            what = "with ignore_permissions" if act.user == "role" else "as Administrator, no real user plays this act"
            findings.append(Finding(path, report.name, act.line, f"{act.doctype} {how}{what}"))
    return findings


# ---------------------------------------------------------------------------- git


def git(root: str, *arguments: str) -> str | None:
    done = subprocess.run(["git", "-C", root, *arguments], capture_output=True, text=True, errors="replace")
    return done.stdout if done.returncode == 0 else None


def holds_a_merge(root: str, base: str) -> bool:
    """A merge of upstream brings hundreds of tests of theirs, all as Administrator: they are not ours to judge, and
    on a fork the check would turn every merge red. On our single branch a merge is never anything else."""
    return bool((git(root, "rev-list", "--merges", f"{base}..HEAD") or "").strip())


def is_test_file(path: str) -> bool:
    return os.path.basename(path).startswith("test_") and path.endswith(".py")


def changed_files(root: str, base: str, staged: bool) -> list[str]:
    arguments = ["diff", "--name-only", "--diff-filter=ACMR"] + (["--cached"] if staged else [])
    listed = git(root, *arguments, base, *([] if staged else ["HEAD"]))
    return [path for path in (listed or "").splitlines() if is_test_file(path)]


def inventory(root: str, functions: dict[str, set[str]], packages: set[str]) -> list[dict]:
    rows = []
    for path in python_files(root):
        if not is_test_file(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            module = read_module(handle.read(), functions, packages)
        if module is None:
            continue
        for class_name, func in module.tests():
            report = judge(module, class_name, func)
            rows.append(
                {
                    "file": os.path.relpath(path, root),
                    "test": report.name,
                    "line": report.line,
                    "kind": report.kind,
                    "doctypes": sorted({a.doctype for a in report.acts}),
                    "via": sorted({a.via for a in report.acts if a.via}),
                    "reasoned": report.allowed,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="files to read from disk, against --base (default HEAD)")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--base", help="the revision a test is new against")
    parser.add_argument("--staged", action="store_true", help="judge the staged files against HEAD")
    parser.add_argument("--inventory", action="store_true", help="say what every test does, refuse nothing")
    parser.add_argument("--json", action="store_true", help="with --inventory: one JSON document")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    packages = app_packages_of(root)
    functions = app_functions(root, packages)
    if args.inventory:
        rows = inventory(root, functions, packages)
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            for kind, count in Counter(row["kind"] for row in rows).most_common():
                print(f"{count:6d} {kind}")
        return 0

    base = "HEAD" if args.staged or args.files else args.base
    if args.base and (args.files or args.staged):
        base = args.base
    if not base:
        parser.error("say what a test is new against: --base <revision>, --staged, or files")

    if not (args.files or args.staged) and holds_a_merge(root, base):
        print("test roles: a merge in the range, upstream's tests are not judged")
        return 0

    findings: list[Finding] = []
    relative = [os.path.relpath(os.path.abspath(path), root) for path in args.files if is_test_file(path)]
    for path in relative or (changed_files(root, base, args.staged) if not args.files else []):
        if args.files:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
                source = handle.read()
        else:
            source = git(root, "show", f":{path}" if args.staged else f"HEAD:{path}")
        if source is None:
            continue
        findings += new_findings(path, source, git(root, "show", f"{base}:{path}"), functions, packages)

    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    for finding in findings:
        text = f"{finding.test}: {finding.what}"
        print(f"::error file={finding.path},line={finding.line}::{text}" if on_github else f"{finding.path}:{finding.line}: {text}")
    if findings:
        print(
            f"\n{len(findings)} new act(s) refused: play them `with as_role(...)` (frappe.tests.roles), or say why not "
            f"on the def line: `# {OK_MARK} <reason>`. Why: scripts/check_test_roles.py of bvisible/neoffice-ci.",
            file=sys.stderr,
        )
        return 1
    print("test roles: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
