#!/usr/bin/env python3
"""A web method asks for a right before it acts: found without running anything.

`@frappe.whitelist()` opens a function to every signed-in account, a customer of the portal included, and it checks
nothing else. An audit of one app confirmed some thirty defects of that one shape, one of them a portal account writing
a file on the server (neoffice-maintenance#2261). An audit costs too much to run on every development: this check costs
nothing, so the family stops where it starts.

The rule, for a method that is NEW or CHANGED (the stock blocks nobody until someone touches it):

  - It holds a permission check, in its own body or in what it calls:
      `frappe.has_permission`, `doc.has_permission`, `doc.check_permission`, `frappe.only_for`, `frappe.get_list`;
      a refusal with `frappe.PermissionError` or `frappe.AuthenticationError`, `raise` or `frappe.throw`;
      a role compared with `frappe.get_roles()`, `has_role`, `user_type` compared with "System User",
      `frappe.session.user` compared with "Administrator";
      `validate_print_permission`, `frappe.has_website_permission`, `frappe.share.add`, `frappe.share.remove`;
      a document of `frappe.get_doc` or `frappe.new_doc` saved, inserted, submitted, cancelled or deleted with its
      check on, and `frappe.delete_doc`: the framework refuses whoever lacks the right. Not on a doctype every account
      may create (OPEN_TO_ALL), where that right says nothing.
  - A helper or a decorator of the app that holds one counts, through MAX_HOPS calls, in the same file or through the
    imports. Refusing guests alone does not count, a signed-in account passes. Neither does a `has_permission` whose
    answer nobody reads, nor a `get_list` or a save with `ignore_permissions`.
  - A new `allow_guest=True` always says why, even if it holds a check: it is a public door.
  - A parameter with no annotation does not reach `frappe.db.get_value`, `frappe.db.exists` or `frappe.get_all` as a
    filter: a JSON body turns it into a list or a dict, which is to say an operator or a filter of the caller's choice.
    Annotated, Frappe validates it; `if not isinstance(name, str): frappe.throw(...)` settles it too, in the method or
    in a helper called before the query. An annotation is always possible, so no reason excuses this one.

A method of a Document class is reached through `run_doc_method`, which asks read on the document: not refused.

A reason says why a method needs no check, on its `def` line, the last line of its signature or above its decorators:

    def get_my_name():  # permission-check: ok the caller's own first name, nothing else

A method is new or changed when its code differs from the base: comments, docstrings and layout are not changes, and a
method moved whole to another file is the same method. A method that relied on a helper of its file and no longer holds
a check is changed too. A range that holds a merge is not judged: on a fork, a merge brings hundreds of upstream methods.

What this check does not see: where the check stands (behind a condition, after the act), whether it asks the right
question, a helper in another file that loses its check while the method does not move. It is a floor, not an audit.

    check_whitelist_permissions.py --base <revision>      every method changed since that revision (CI)
    check_whitelist_permissions.py --staged               what is staged, against HEAD
    check_whitelist_permissions.py [--base HEAD] file...  these files as they are on disk (pre-commit)
    check_whitelist_permissions.py --inventory [--json]   what every method of the repository holds, nothing is refused

Nothing is imported or executed: the files are parsed. Exit 1 when a method is refused.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import sys
import tokenize
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

OK_MARK = "permission-check: ok"
MIN_REASON = 10  # a reason, not "ok" or "n/a"
MAX_HOPS = 2  # `_guard()` calling `_require_desk_user()`: further than that a check is about something else
MAX_NAME_CHAIN = 4  # how many aliases and re-exports a name goes through
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", ".tox", ".mypy_cache"}

ASKS = {"has_permission", "has_website_permission", "has_role"}  # say yes or no: the answer must be read
REFUSES = {"check_permission", "only_for", "validate_print_permission"}  # stop the request by themselves
CHECKED_CALLS = {"frappe.get_list", "frappe.db.get_list", "frappe.share.add", "frappe.share.remove"}  # unless told not to
REFUSALS = {"PermissionError", "AuthenticationError"}
SAVE_VERBS = {"save", "insert", "submit", "cancel", "delete"}
DOC_SOURCES = {"frappe.get_doc", "frappe.new_doc"}
# The doctypes of frappe and erpnext whose role `All` may create: a portal customer passes their check.
OPEN_TO_ALL = {
    "Address",
    "Contact",
    "File",
    "Notification Settings",
    "Tag",
    "Tag Link",
    "ToDo",
    "Video",
    "Voice Call Settings",
}
DESK_USER = "System User"
ADMIN = "Administrator"
SESSION_USER = "frappe.session.user"

# Where the filter stands in the positional arguments. frappe.get_all(doctype, fields, filters): `fields` comes first.
FILTER_POSITION = {
    "frappe.db.get_value": 1,
    "frappe.get_value": 1,
    "frappe.db.get_values": 1,
    "frappe.db.exists": 1,
    "frappe.get_all": 2,
    "frappe.db.get_all": 2,
}
FILTER_KEYWORDS = {"filters", "or_filters", "dn"}
# Give back what they are given, or a part of it: anything else (`str`, `cstr`, `.strip()`) makes it a text or fails.
HANDS_ON = {"frappe.parse_json", "parse_json", "json.loads", "loads", "frappe._dict", "dict", "list", "tuple", "set"}
HANDS_ON_METHODS = {"get", "pop", "copy"}
FILLS = {"update", "append", "extend", "add", "setdefault", "insert"}
PLAIN_TYPES = {"str", "int", "float"}


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _last_name(node: ast.AST) -> str:
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and not node.value


def _permissions_off(call: ast.Call) -> bool:
    value = _keyword(call, "ignore_permissions")
    return value is not None and not _is_false(value)


# ---------------------------------------------------------------------------- what a function holds itself


def _refusal(node: ast.AST) -> str:
    """The exception this node refuses with, "" when it refuses nothing a right decides."""
    if isinstance(node, ast.Raise) and node.exc is not None:
        candidates = [node.exc.func if isinstance(node.exc, ast.Call) else node.exc]
    elif isinstance(node, ast.Call) and _last_name(node.func) == "throw":
        candidates = [*node.args, *(keyword.value for keyword in node.keywords)]
    else:
        return ""
    return next((_last_name(c) for c in candidates if _last_name(c) in REFUSALS), "")


def _tells_guests_only(test: ast.expr) -> bool:
    """`if frappe.session.user == "Guest":`, `if not user or user == "Guest":`: a signed-in account goes through."""
    guest = False
    for part in test.values if isinstance(test, ast.BoolOp) else [test]:
        if isinstance(part, ast.Compare) and any(
            isinstance(n, ast.Constant) and n.value == "Guest" for n in ast.walk(part)
        ):
            guest = True
        elif not (isinstance(part, ast.UnaryOp) and isinstance(part.operand, (ast.Name, ast.Attribute))):
            return False
    return guest


def _asks_for_roles(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _last_name(node.func) == "get_roles"


def _compares_roles(nodes: list[ast.AST]) -> bool:
    """`"Stock User" in frappe.get_roles()`, through a variable or a set. `return frappe.get_roles()` decides nothing."""
    names = {
        target.id
        for node in nodes
        if isinstance(node, ast.Assign) and any(_asks_for_roles(n) for n in ast.walk(node.value))
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def holds_roles(node: ast.AST) -> bool:
        return any(_asks_for_roles(n) or (isinstance(n, ast.Name) and n.id in names) for n in ast.walk(node))

    for node in nodes:
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            if any(holds_roles(side) for side in node.comparators):
                return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
            if holds_roles(node.left) or holds_roles(node.right):
                return True
    return False


def _compares_identity(nodes: list[ast.AST]) -> bool:
    """`user_type != "System User"`, `frappe.session.user != "Administrator"`. A variable named `user` says nothing:
    `if user == "Administrator": continue` is a loop over users."""
    for node in nodes:
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            values = {side.value for side in sides if isinstance(side, ast.Constant)}
            if DESK_USER in values or (ADMIN in values and any(_dotted(side) == SESSION_USER for side in sides)):
                return True
    return False


def _doctype_named(node: ast.AST | None, names: dict[str, str]) -> str:
    """The doctype this value of frappe.get_doc names, "" when the code does not say."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id, "")
    value = None
    if isinstance(node, ast.Dict):
        value = next(
            (v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == "doctype"), None
        )
    elif isinstance(node, ast.Call) and _dotted(node.func) == "dict":
        value = _keyword(node, "doctype")
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else ""


def _saves_with_its_check(nodes: list[ast.AST]) -> bool:
    """`doc.save()` refuses whoever lacks the right, unless it is told not to: `doc.flags.ignore_permissions = True`
    and `frappe.flags.ignore_permissions` turn the check off for the whole function. It proves nothing on a doctype
    every account may create: `frappe.get_doc({"doctype": "File", ...}).insert()` attached any file to any document."""
    if any(isinstance(node, ast.Attribute) and node.attr == "ignore_permissions" for node in nodes):
        return False
    assigned = [node for node in nodes if isinstance(node, ast.Assign)]
    names = {
        target.id: _doctype_named(node.value, {})
        for node in assigned
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def gives_a_guarded_doc(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call) or _dotted(node.func) not in DOC_SOURCES:
            return False
        return _doctype_named(node.args[0] if node.args else None, names) not in OPEN_TO_ALL

    docs = {
        target.id
        for node in assigned
        if gives_a_guarded_doc(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in nodes:
        if not isinstance(node, ast.Call) or _permissions_off(node):
            continue
        if _dotted(node.func) == "frappe.delete_doc":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in SAVE_VERBS:
            receiver = node.func.value
            if (isinstance(receiver, ast.Name) and receiver.id in docs) or gives_a_guarded_doc(receiver):
                return True
    return False


def own_control(func: ast.AST) -> str:
    """The check this function holds in its own body, "" when it holds none."""
    nodes = list(ast.walk(func))
    dropped = {id(node.value) for node in nodes if isinstance(node, ast.Expr)}
    behind_a_guest_test = {
        id(inner)
        for gate in nodes
        if isinstance(gate, ast.If) and _tells_guests_only(gate.test)
        for statement in gate.body
        for inner in ast.walk(statement)
    }
    for node in nodes:
        refusal = _refusal(node)
        if refusal and id(node) not in behind_a_guest_test:
            return refusal
        if not isinstance(node, ast.Call):
            continue
        name, last = _dotted(node.func), _last_name(node.func)
        if last in REFUSES:
            return name or last
        throws = _keyword(node, "throw")
        if last in ASKS and (id(node) not in dropped or (throws is not None and not _is_false(throws))):
            return name or last
        if name in CHECKED_CALLS and not _permissions_off(node) and _keyword(node, "flags") is None:
            return name
    if _compares_roles(nodes):
        return "roles compared"
    if _compares_identity(nodes):
        return "identity compared"
    if _saves_with_its_check(nodes):
        return "save with its check"
    return ""


# ---------------------------------------------------------------------------- what a parameter reaches


@dataclass
class Filter:
    parameter: str
    call: str
    line: int


def _open_parameters(func: ast.AST) -> list[str]:
    arguments = func.args
    named = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    named += [extra for extra in (arguments.vararg, arguments.kwarg) if extra]
    return [one.arg for one in named if one.annotation is None and one.arg not in ("self", "cls")]


def _stops(statements: list[ast.stmt]) -> bool:
    last = statements[-1]
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        return _last_name(last.value.func) == "throw"
    return isinstance(last, (ast.Raise, ast.Return))


def _refuses_non_text(statement: ast.stmt) -> str:
    """The name that `if not isinstance(name, str): frappe.throw(...)` lets on as a plain value, "" for any other
    statement. `if isinstance(filters, str): filters = parse_json(filters)` is the opposite: it makes a filter."""
    if not isinstance(statement, ast.If) or statement.orelse or not _stops(statement.body):
        return ""
    test = statement.test
    if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Call)):
        return ""
    call = test.operand
    if _dotted(call.func) != "isinstance" or len(call.args) != 2 or not isinstance(call.args[0], ast.Name):
        return ""
    accepted = call.args[1].elts if isinstance(call.args[1], ast.Tuple) else [call.args[1]]
    return call.args[0].id if all(_dotted(one) in PLAIN_TYPES for one in accepted) else ""


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


class FilterWalk:
    """Reads a function in order and follows its parameters with no annotation to the filters of a query."""

    def __init__(self, func: ast.AST, resolve: Callable[[ast.AST], tuple[ast.AST, Source] | None] | None = None):
        self.carried = {name: name for name in _open_parameters(func)}  # a variable: the parameter it holds
        self.found: list[Filter] = []
        self.resolve = resolve or (lambda _node: None)
        self.block(func.body)

    def block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.statement(statement)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.expression(node.iter)
            self.bind(node.target, node.iter)
            self.block(node.body)
            self.block(node.orelse)
        elif isinstance(node, ast.Assign):
            self.expression(node.value)
            for target in node.targets:
                self.bind(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            if node.value:
                self.expression(node.value)
                self.bind(node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            self.expression(node.value)
            if self.carried_by(node.value):
                self.bind(node.target, node.value)
        elif isinstance(node, (ast.If, ast.While)):
            self.expression(node.test)
            self.block(node.body)
            self.block(node.orelse)
            self.carried.pop(_refuses_non_text(node), None)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.expression(item.context_expr)
            self.block(node.body)
        elif hasattr(node, "handlers"):  # try, and try* from Python 3.11
            self.block(node.body)
            for handler in node.handlers:
                self.block(handler.body)
            self.block(node.orelse)
            self.block(node.finalbody)
        else:
            self.expression(node)

    def expression(self, node: ast.AST) -> None:
        nodes = list(ast.walk(node))
        for inner in nodes:
            if isinstance(inner, ast.comprehension):
                self.bind(inner.target, inner.iter)
        for inner in nodes:
            if isinstance(inner, ast.Call):
                self.filled(inner)
                self.settled_by_a_helper(inner)
                self.query(inner)

    def bind(self, target: ast.AST, value: ast.AST) -> None:
        parameter = self.carried_by(value)
        if isinstance(target, ast.Name):
            if parameter:
                self.carried[target.id] = parameter
            else:
                self.carried.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.bind(element, value)
        elif parameter and _root_name(target):  # filters["title"] = name
            self.carried[_root_name(target)] = parameter

    def filled(self, call: ast.Call) -> None:
        """`filters.update({"title": name})`"""
        if isinstance(call.func, ast.Attribute) and call.func.attr in FILLS and _root_name(call.func.value):
            for part in [*call.args, *(keyword.value for keyword in call.keywords)]:
                parameter = self.carried_by(part)
                if parameter:
                    self.carried[_root_name(call.func.value)] = parameter

    def settled_by_a_helper(self, call: ast.Call) -> None:
        """`_require_note_name(note)`, whose body refuses anything but a text."""
        found = self.resolve(call.func)
        if found:
            parameters = found[0].args.args
            for argument, parameter in zip(call.args, parameters):
                if isinstance(argument, ast.Name) and any(
                    _refuses_non_text(statement) == parameter.arg for statement in found[0].body
                ):
                    self.carried.pop(argument.id, None)

    def carried_by(self, node: ast.AST) -> str:
        """The parameter this expression may still be, "" when it can only be something else."""
        if isinstance(node, ast.Name):
            return self.carried.get(node.id, "")
        if isinstance(node, ast.Dict):
            parts = [*(key for key in node.keys if key), *node.values]
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            parts = node.elts
        elif isinstance(node, ast.IfExp):
            parts = [node.body, node.orelse]
        elif isinstance(node, ast.BoolOp):
            parts = node.values
        elif isinstance(node, (ast.Starred, ast.NamedExpr, ast.Subscript)):
            parts = [node.value]
        elif isinstance(node, ast.Call) and _dotted(node.func) in HANDS_ON:
            parts = node.args[:1]
        elif isinstance(node, ast.Call) and _last_name(node.func) in HANDS_ON_METHODS and isinstance(node.func, ast.Attribute):
            parts = [node.func.value]
        else:
            parts = []
        return next((found for found in map(self.carried_by, parts) if found), "")

    def query(self, call: ast.Call) -> None:
        name = _dotted(call.func)
        if name not in FILTER_POSITION:
            return
        position = FILTER_POSITION[name]
        if name == "frappe.db.exists" and len(call.args) == 1 and _keyword(call, "dn") is None:
            position = 0  # frappe.db.exists({"doctype": "Note", "title": title})
        candidates = [*call.args[position : position + 1]]
        candidates += [keyword.value for keyword in call.keywords if keyword.arg in FILTER_KEYWORDS]
        for candidate in candidates:
            parameter = self.carried_by(candidate)
            if parameter and not any(f.parameter == parameter and f.call == name for f in self.found):
                self.found.append(Filter(parameter, name, call.lineno))


# ---------------------------------------------------------------------------- what the names of a file stand for


class Source:
    """One parsed file: its functions, and what its other names stand for."""

    def __init__(self, text: str, module: str = "", is_package: bool = False):
        self.tree = ast.parse(text)
        self.lines = text.splitlines()
        self.module = module
        self.is_package = is_package
        self.functions: dict[str, ast.AST] = {}
        self.aliases: dict[str, ast.expr] = {}  # _require = permitted.require_desk_user
        self.read_level(self.tree.body)
        self.imports = self.read_imports()  # a local name: the dotted name it stands for

    def read_level(self, statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and _dotted(node.value):
                    self.aliases[node.targets[0].id] = node.value
            elif not isinstance(node, ast.ClassDef):
                for block in _blocks(node):
                    self.read_level(block)

    def absolute(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        package = self.module.split(".") if self.is_package else self.module.split(".")[:-1]
        package = package[: len(package) - (node.level - 1)]
        return ".".join([*package, *([node.module] if node.module else [])])

    def read_imports(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for node in ast.walk(self.tree):  # an import inside a function counts: they are common in Frappe apps
            if isinstance(node, ast.ImportFrom):
                origin = self.absolute(node)
                names.update({alias.asname or alias.name: f"{origin}.{alias.name}" for alias in node.names})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        names[alias.asname] = alias.name
                    else:
                        names[alias.name.split(".")[0]] = alias.name.split(".")[0]
        return names


def _blocks(node: ast.stmt) -> Iterator[list[ast.stmt]]:
    for name in ("body", "orelse", "finalbody"):
        block = getattr(node, name, None)
        if isinstance(block, list):
            yield block
    for handler in getattr(node, "handlers", []):
        yield handler.body


def _module_of(path: str) -> tuple[str, bool]:
    """(dotted name of a file of the repository, whether it is a package)."""
    dotted = path[: -len(".py")].replace(os.sep, ".")
    return (dotted[: -len(".__init__")], True) if dotted.endswith(".__init__") else (dotted, False)


class Tree:
    """The app at one moment: a file is read and parsed when a call reaches it, so a large fork costs what it is used."""

    def __init__(self, read: Callable[[str], str | None] | None = None):
        self.read = read or (lambda _path: None)
        self.sources: dict[str, Source | None] = {}
        self.controls: dict[tuple[ast.AST, int], str] = {}

    def adopt(self, path: str, text: str) -> Source | None:
        if path not in self.sources:
            try:
                self.sources[path] = Source(text, *_module_of(path))
            except (SyntaxError, ValueError):
                self.sources[path] = None  # not this script's business: the test run reports it
        return self.sources[path]

    def source(self, module: str) -> Source | None:
        """By path, not by module: `x.py` and `x/__init__.py` can stand side by side, and Python imports the package."""
        parts = module.split(".")
        for path in (os.path.join(*parts, "__init__.py"), os.path.join(*parts) + ".py"):
            text = self.read(path) if path not in self.sources else ""
            if text is not None:
                return self.adopt(path, text)
        return None

    def resolve(self, node: ast.AST, source: Source, chain: int = 0) -> tuple[ast.AST, Source] | None:
        """The function of the app this name stands for, None when it is anything else."""
        name = _dotted(node)
        if not name or chain > MAX_NAME_CHAIN:
            return None
        head, _, rest = name.partition(".")
        if not rest and head in source.functions:
            return source.functions[head], source
        if not rest and head in source.aliases:
            return self.resolve(source.aliases[head], source, chain + 1)
        if head in source.imports:
            module, _, last = ".".join(filter(None, (source.imports[head], rest))).rpartition(".")
            found = self.source(module) if module else None
            return self.resolve(ast.Name(last), found, chain + 1) if found else None
        return None

    def control(self, func: ast.AST, source: Source, hops: int = MAX_HOPS) -> str:
        """The check this function holds or reaches, "" when it reaches none."""
        key = (func, hops)
        if key not in self.controls:
            self.controls[key] = own_control(func) or (self.through_calls(func, source, hops) if hops else "")
        return self.controls[key]

    def through_calls(self, func: ast.AST, source: Source, hops: int) -> str:
        decorators = [d.func if isinstance(d, ast.Call) else d for d in func.decorator_list]
        calls = [node.func for node in ast.walk(func) if isinstance(node, ast.Call)]
        for target in (*decorators, *calls):
            found = self.resolve(target, source)
            control = self.control(found[0], found[1], hops - 1) if found else ""
            if control:
                return f"{_dotted(target)} > {control}"
        return ""


# ---------------------------------------------------------------------------- the web methods of a file


@dataclass
class Method:
    name: str  # Class.method or function
    line: int
    kind: str  # direct, helper, document, none
    control: str
    guest: bool
    reasoned: bool
    fingerprint: str
    filters: list[Filter] = field(default_factory=list)


@dataclass
class Finding:
    path: str
    method: str
    line: int
    what: str


def _whitelist_of(func: ast.AST) -> ast.expr | None:
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _dotted(target) in ("frappe.whitelist", "whitelist"):
            return decorator
    return None


def _opens_to_guests(decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    value = _keyword(decorator, "allow_guest")
    value = value if value is not None else (decorator.args[0] if decorator.args else None)
    return value is not None and not _is_false(value)


def web_methods(body: list[ast.stmt], prefix: str = "", in_class: bool = False) -> Iterator[tuple[str, ast.AST, bool]]:
    """(qualified name, function, whether it is a method of a class) of every web method of these statements."""
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from web_methods(node.body, f"{prefix}{node.name}.", True)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _whitelist_of(node) is not None:
                yield f"{prefix}{node.name}", node, in_class
            yield from web_methods(node.body, f"{prefix}{node.name}.")
        else:
            for block in _blocks(node):
                yield from web_methods(block, prefix, in_class)


def fingerprint(func: ast.AST) -> str:
    """What the method is, without its comments, its docstring and its layout."""
    body = func.body[1:] if ast.get_docstring(func, clean=False) is not None else func.body
    return func.name + "".join(ast.dump(part) for part in (*func.decorator_list, func.args, *body))


def fingerprints(text: str | None) -> set[str]:
    if text is None:
        return set()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    return {fingerprint(func) for _name, func, _in_class in web_methods(tree.body)}


def _reason_line(source_line: str) -> bool:
    return OK_MARK in source_line and len(source_line.split(OK_MARK, 1)[1].strip()) >= MIN_REASON


def _signature_end(func: ast.AST, lines: list[str]) -> int:
    """The line of the colon that ends a signature: the AST only says where the body starts, comments stand between."""
    text = "\n".join(lines[func.lineno - 1 :])
    depth = 0
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.OP:
            depth += (token.string in "([{") - (token.string in ")]}")
            if token.string == ":" and depth == 0:
                return func.lineno + token.start[0] - 1
    return func.lineno


def has_reason(func: ast.AST, lines: list[str]) -> bool:
    first = min([func.lineno, *(decorator.lineno for decorator in func.decorator_list)])
    return any(_reason_line(line) for line in lines[max(first - 2, 0) : _signature_end(func, lines)])


def read_methods(path: str, text: str, tree: Tree) -> dict[str, Method] | None:
    source = tree.adopt(path, text)
    if source is None:
        return None
    methods = {}
    for name, func, in_class in web_methods(source.tree.body):
        control = tree.control(func, source)
        if in_class:
            kind = "document"
        elif not control:
            kind = "none"
        else:
            kind = "helper" if " > " in control else "direct"
        methods[name] = Method(
            name,
            func.lineno,
            kind,
            control,
            _opens_to_guests(_whitelist_of(func)),
            has_reason(func, source.lines),
            fingerprint(func),
            FilterWalk(func, lambda node, source=source: tree.resolve(node, source)).found,
        )
    return methods


def what_is_refused(method: Method, newly_open: bool) -> list[tuple[int, str]]:
    refused = []
    if newly_open and not method.reasoned:
        refused.append((method.line, "open to guests: say why it is public on its def line"))
    elif method.kind == "none" and not method.reasoned:
        refused.append((method.line, "no permission check: any signed-in account reaches it, a portal customer included"))
    for found in method.filters:
        refused.append((
            found.line,
            f"parameter '{found.parameter}' reaches {found.call} as a filter with no annotation: "
            "a JSON body makes it an operator, annotate it",
        ))
    return refused


def new_findings(
    path: str,
    text: str,
    base_text: str | None,
    now: Tree | None = None,
    before: Tree | None = None,
    moved: Iterable[str] = frozenset(),
) -> list[Finding]:
    """What this version of a file adds and the rule refuses; `base_text` is the file before, None when it is new."""
    current = read_methods(path, text, now or Tree())
    if current is None:
        return []
    previous = read_methods(path, base_text, before or Tree()) if base_text is not None else None
    findings = []
    for method in current.values():
        old = (previous or {}).get(method.name)
        same = method.fingerprint in moved or (old is not None and old.fingerprint == method.fingerprint)
        lost_its_check = old is not None and old.kind != "none" and method.kind == "none"
        if same and not lost_its_check:
            continue  # the stock is rewritten when someone chooses to
        newly_open = method.guest and not (old is not None and old.guest)
        findings += [Finding(path, method.name, line, what) for line, what in what_is_refused(method, newly_open)]
    return findings


# ---------------------------------------------------------------------------- git


def git(root: str, *arguments: str) -> str | None:
    done = subprocess.run(["git", "-C", root, *arguments], capture_output=True, text=True, errors="replace")
    return done.stdout if done.returncode == 0 else None


def holds_a_merge(root: str, base: str) -> bool:
    """A merge of upstream brings hundreds of their methods: they are not ours to judge, and on a fork the check would
    turn every merge red. On our single branch a merge is never anything else."""
    return bool((git(root, "rev-list", "--merges", f"{base}..HEAD") or "").strip())


def is_judged(path: str) -> bool:
    name = os.path.basename(path)
    parts = path.split(os.sep)
    return name.endswith(".py") and not name.startswith("test_") and name != "conftest.py" and "tests" not in parts[:-1]


def changed_files(root: str, base: str, staged: bool, on_disk: bool) -> list[str]:
    """--no-renames: a renamed file is one deleted and one added, so its methods are found moved."""
    arguments = ["diff", "--no-renames", "--name-only"]
    arguments += ["--cached", base] if staged else [base] if on_disk else [base, "HEAD"]
    return [path for path in (git(root, *arguments) or "").splitlines() if is_judged(path)]


def moved_methods(
    paths: list[str], read_now: Callable[[str], str | None], read_base: Callable[[str], str | None]
) -> set[str]:
    """The methods that left a file of this change: found elsewhere, they are the same methods."""
    moved: set[str] = set()
    for path in paths:
        moved |= fingerprints(read_base(path)) - fingerprints(read_now(path))
    return moved


def python_files(root: str) -> Iterator[str]:
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(folder, name)


def reader_of(root: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    return read


def inventory(root: str) -> list[dict]:
    read = reader_of(root)
    tree = Tree(read)
    rows = []
    for path in python_files(root):
        relative = os.path.relpath(path, root)
        if not is_judged(relative):
            continue
        for method in (read_methods(relative, read(relative) or "", tree) or {}).values():
            rows.append(
                {
                    "file": relative,
                    "method": method.name,
                    "line": method.line,
                    "kind": method.kind,
                    "control": method.control,
                    "guest": method.guest,
                    "reasoned": method.reasoned,
                    "filters": [vars(found) for found in method.filters],
                }
            )
    return rows


def print_inventory(rows: list[dict]) -> None:
    for kind in ("direct", "helper", "document", "none"):
        print(f"{sum(row['kind'] == kind for row in rows):6d} {kind}")
    print(f"{len(rows):6d} web methods")
    print(f"{sum(row['guest'] for row in rows):6d} open to guests")
    print(f"{sum(row['guest'] and row['kind'] == 'none' for row in rows):6d} open to guests with no check")
    print(f"{sum(bool(row['filters']) for row in rows):6d} with a parameter that reaches a filter")
    print(f"{sum(row['reasoned'] for row in rows):6d} with a reason")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", help="files to read from disk, against --base (default HEAD)")
    parser.add_argument("--root", default=".", help="the repository (default: the current directory)")
    parser.add_argument("--base", help="the revision a method is new against")
    parser.add_argument("--staged", action="store_true", help="judge the staged files against HEAD")
    parser.add_argument("--inventory", action="store_true", help="say what every method holds, refuse nothing")
    parser.add_argument("--json", action="store_true", help="with --inventory: one JSON document")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if args.inventory:
        rows = inventory(root)
        if args.json:
            print(json.dumps(rows, indent=1))
        else:
            print_inventory(rows)
        return 0

    base = args.base or ("HEAD" if args.staged or args.files else None)
    if not base:
        parser.error("say what a method is new against: --base <revision>, --staged, or files")

    on_disk = bool(args.files)
    if not on_disk and not args.staged and holds_a_merge(root, base):
        print("whitelist permissions: a merge in the range, upstream's methods are not judged")
        return 0

    if on_disk:
        read_now = reader_of(root)
    else:
        revision = "" if args.staged else "HEAD"

        def read_now(path: str) -> str | None:
            return git(root, "show", f"{revision}:{path}")

    def read_base(path: str) -> str | None:
        return git(root, "show", f"{base}:{path}")

    changed = changed_files(root, base, args.staged, on_disk)
    named = [os.path.relpath(os.path.abspath(path), root) for path in args.files]
    moved = moved_methods(changed, read_now, read_base)
    now, before = Tree(read_now), Tree(read_base)
    findings: list[Finding] = []
    for path in [path for path in named if is_judged(path)] if on_disk else changed:
        text = read_now(path)
        if text is not None:
            findings += new_findings(path, text, read_base(path), now, before, moved)

    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    for finding in findings:
        text = f"{finding.method}: {finding.what}"
        print(f"::error file={finding.path},line={finding.line}::{text}" if on_github else f"{finding.path}:{finding.line}: {text}")
    if findings:
        print(
            f"\n{len(findings)} refused: ask for the right (frappe.has_permission, doc.check_permission, frappe.only_for, "
            f"frappe.get_list), or say why not on the def line: `# {OK_MARK} <reason>`. "
            "Why: scripts/check_whitelist_permissions.py of bvisible/neoffice-ci.",
            file=sys.stderr,
        )
        return 1
    print("whitelist permissions: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
