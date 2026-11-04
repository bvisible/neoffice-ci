#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_whitelist_permissions as check  # noqa: E402

HEAD = "import frappe\nfrom frappe import _\n\n"


def source(body: str) -> str:
    return HEAD + textwrap.dedent(body).strip("\n") + "\n"


def refused(body: str, base: str | None = None, **kwargs) -> list[str]:
    return [f.what for f in check.new_findings("app/api.py", source(body), base and source(base), **kwargs)]


def method(body: str) -> str:
    """A whitelisted method around these lines: the parameter is annotated, its filters are not the question."""
    lines = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return f"@frappe.whitelist()\ndef read_note(name: str):\n{lines}\n"


CHECKS = {
    "has_permission that throws": """
        frappe.has_permission("Note", "read", name, throw=True)
    """,
    "has_permission that is asked": """
        if not frappe.has_permission("Note", "read", name):
            return {}
    """,
    "has_permission of a document": """
        doc = frappe.get_doc("Note", name)
        if not doc.has_permission("read"):
            return {}
    """,
    "check_permission": """
        frappe.get_doc("Note", name).check_permission("read")
    """,
    "only_for": """
        frappe.only_for("Stock Manager")
    """,
    "a read by get_list": """
        return frappe.get_list("Note", fields=["name"])
    """,
    "a read by frappe.db.get_list": """
        return frappe.db.get_list("Note", fields=["name"])
    """,
    "a raised PermissionError": """
        if not owns(name):
            raise frappe.PermissionError
    """,
    "a raised PermissionError with a message": """
        if not owns(name):
            raise frappe.PermissionError(_("Not yours"))
    """,
    "frappe.throw with a PermissionError": """
        if not owns(name):
            frappe.throw(_("Not yours"), frappe.PermissionError)
    """,
    "frappe.throw with exc=": """
        if not owns(name):
            frappe.throw(_("Not yours"), exc=frappe.PermissionError)
    """,
    "a credential refused": """
        if not token_is_valid(name):
            frappe.throw(_("Unknown terminal"), frappe.AuthenticationError)
    """,
    "a guest and more refused": """
        if frappe.session.user == "Guest" or not is_staff(frappe.session.user):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    """,
    "a role in frappe.get_roles": """
        if "Stock User" not in frappe.get_roles():
            return {}
    """,
    "a role in a variable": """
        roles = frappe.get_roles()
        if "Stock User" in roles:
            return {"ok": 1}
    """,
    "roles crossed with a set": """
        if not set(READERS) & set(frappe.get_roles()):
            return {}
    """,
    "has_role": """
        if not frappe.user.has_role("Stock User"):
            return {}
    """,
    "the desk user test": """
        if frappe.db.get_value("User", frappe.session.user, "user_type") != "System User":
            return {}
    """,
    "the session user compared with Administrator": """
        if frappe.session.user != "Administrator":
            frappe.throw(_("Only Administrator"))
    """,
    "the print right": """
        validate_print_permission(frappe.get_doc("Note", name))
    """,
    "the website right": """
        if not frappe.has_website_permission(frappe.get_doc("Note", name)):
            return {}
    """,
    "a save with its check on": """
        doc = frappe.get_doc("Note", name)
        doc.title = "x"
        doc.save()
    """,
    "an insert with its check on": """
        frappe.get_doc({"doctype": "Note", "title": name}).insert()
    """,
    "a new document saved": """
        doc = frappe.new_doc("Note")
        doc.submit()
    """,
    "a share, whose right frappe asks": """
        frappe.share.add("Note", name, "someone@example.com", read=1)
    """,
    "a share taken back": """
        frappe.share.remove("Note", name, "someone@example.com")
    """,
    "delete_doc with its check on": """
        frappe.delete_doc("Note", name)
    """,
}

NOT_CHECKS = {
    "nothing at all": """
        return frappe.get_doc("Note", name).as_dict()
    """,
    "a read by get_all": """
        return frappe.get_all("Note", fields=["name"])
    """,
    "get_list with its check off": """
        return frappe.get_list("Note", fields=["name"], ignore_permissions=True)
    """,
    "has_permission whose answer is dropped": """
        frappe.has_permission("Note", "read", name)
        return frappe.get_doc("Note", name)
    """,
    "has_permission of a document whose answer is dropped": """
        doc = frappe.get_doc("Note", name)
        doc.has_permission("read")
    """,
    "a guest refused and nobody else": """
        if frappe.session.user == "Guest":
            frappe.throw(_("Please log in."), frappe.PermissionError)
    """,
    "a guest refused by raise": """
        user = frappe.session.user
        if not user or user == "Guest":
            raise frappe.PermissionError
    """,
    "a guest refused from a list": """
        if frappe.session.user in ("Guest", None):
            raise frappe.PermissionError
    """,
    "roles handed back": """
        return frappe.get_roles()
    """,
    "a refusal of another kind": """
        if not name:
            frappe.throw(_("A name"), frappe.ValidationError)
    """,
    "a save with its check off": """
        doc = frappe.get_doc("Note", name)
        doc.save(ignore_permissions=True)
    """,
    "a save behind flags.ignore_permissions": """
        doc = frappe.get_doc("Note", name)
        doc.flags.ignore_permissions = True
        doc.save()
    """,
    "the save of something else": """
        image = render(name)
        image.save("/tmp/note.png")
    """,
    "a share with its check off": """
        frappe.share.add("Note", name, "someone@example.com", flags={"ignore_share_permission": True})
    """,
    "a file inserted, which every account may": """
        frappe.get_doc({"doctype": "File", "file_url": name}).insert()
    """,
    "a file inserted through a dictionary": """
        values = {"doctype": "File", "file_url": name}
        doc = frappe.get_doc(values)
        doc.insert()
    """,
    "a file inserted through dict()": """
        doc = frappe.get_doc(dict(doctype="File", file_url=name))
        doc.insert()
    """,
    "a task of a new_doc, which every account may": """
        doc = frappe.new_doc("ToDo")
        doc.insert()
    """,
    "delete_doc with its check off": """
        frappe.delete_doc("Note", name, ignore_permissions=True)
    """,
    "Administrator skipped in a list of users": """
        for user in frappe.get_all("User", pluck="name"):
            if user == "Administrator":
                continue
            notify(user, name)
    """,
    "set_value": """
        frappe.db.set_value("Note", name, "title", "x")
    """,
}


class TheRule(unittest.TestCase):
    def test_a_new_method_with_no_check_is_refused(self):
        found = refused(method('return frappe.get_doc("Note", name).as_dict()'))
        self.assertEqual(len(found), 1)
        self.assertIn("no permission check", found[0])

    def test_every_check_that_counts(self):
        for label, body in CHECKS.items():
            with self.subTest(label):
                self.assertEqual(refused(method(body)), [])

    def test_what_looks_like_a_check_and_is_not(self):
        for label, body in NOT_CHECKS.items():
            with self.subTest(label):
                self.assertEqual(len(refused(method(body))), 1)

    def test_a_function_that_is_not_a_web_method_is_not_judged(self):
        self.assertEqual(refused("""
            def read_note(name):
                return frappe.get_doc("Note", name)
        """), [])

    def test_whitelist_imported_by_name_is_judged(self):
        found = check.new_findings(
            "app/api.py",
            'from frappe import whitelist\n\n@whitelist()\ndef read_note():\n    return 1\n',
            None,
        )
        self.assertEqual(len(found), 1)

    def test_whitelist_below_another_decorator_is_judged(self):
        self.assertEqual(len(refused("""
            @rate_limit(limit=5, seconds=60)
            @frappe.whitelist()
            def read_note():
                return 1
        """)), 1)

    def test_the_finding_names_the_method_and_its_line(self):
        found = check.new_findings("app/api.py", source(method("return 1")), None)
        self.assertEqual((found[0].path, found[0].method, found[0].line), ("app/api.py", "read_note", 5))

    def test_a_method_of_a_document_is_asked_read_by_the_framework(self):
        self.assertEqual(refused("""
            class Note(Document):
                @frappe.whitelist()
                def archive(self):
                    self.db_set("archived", 1)
        """), [])

    def test_a_file_that_does_not_parse_is_left_to_the_tests(self):
        self.assertEqual(check.new_findings("app/api.py", "def broken(:\n", None), [])


class TheHelpers(unittest.TestCase):
    def test_a_helper_of_the_same_file_that_checks(self):
        self.assertEqual(refused("""
            def _require(doctype):
                if not frappe.has_permission(doctype, "read"):
                    frappe.throw(_("Not permitted"))

            @frappe.whitelist()
            def read_note():
                _require("Note")
                return 1
        """), [])

    def test_a_helper_that_checks_nothing_is_no_check(self):
        self.assertEqual(len(refused("""
            def _clean(name):
                return name.strip()

            @frappe.whitelist()
            def read_note(name: str):
                return _clean(name)
        """)), 1)

    def test_two_levels_are_followed(self):
        self.assertEqual(refused("""
            def _require_staff():
                frappe.only_for("Stock User")

            def _guard():
                _require_staff()

            @frappe.whitelist()
            def read_note():
                _guard()
                return 1
        """), [])

    def test_three_levels_are_too_far(self):
        self.assertEqual(len(refused("""
            def _third():
                frappe.only_for("Stock User")

            def _second():
                _third()

            def _first():
                _second()

            @frappe.whitelist()
            def read_note():
                _first()
                return 1
        """)), 1)

    def test_two_helpers_that_call_each_other_end(self):
        self.assertEqual(len(refused("""
            def _one():
                _two()

            def _two():
                _one()

            @frappe.whitelist()
            def read_note():
                _one()
                return 1
        """)), 1)

    def test_a_helper_named_at_module_level(self):
        self.assertEqual(refused("""
            def require_staff():
                frappe.only_for("Stock User")

            _require = require_staff

            @frappe.whitelist()
            def read_note():
                _require()
                return 1
        """), [])

    def test_a_decorator_of_the_file_that_checks(self):
        self.assertEqual(refused("""
            def staff_only(fn):
                def wrapper(*args, **kwargs):
                    frappe.only_for("Stock User")
                    return fn(*args, **kwargs)
                return wrapper

            @frappe.whitelist()
            @staff_only
            def read_note():
                return 1
        """), [])

    def test_a_decorator_that_takes_arguments(self):
        self.assertEqual(refused("""
            def needs(role):
                def decorate(fn):
                    def wrapper(*args, **kwargs):
                        frappe.only_for(role)
                        return fn(*args, **kwargs)
                    return wrapper
                return decorate

            @frappe.whitelist()
            @needs("Stock User")
            def read_note():
                return 1
        """), [])

    def test_a_helper_defined_in_a_condition(self):
        self.assertEqual(refused("""
            if frappe.__version__:
                def _require():
                    frappe.only_for("Stock User")

            @frappe.whitelist()
            def read_note():
                _require()
                return 1
        """), [])


PERMITTED = 'import frappe\n\ndef require_staff():\n    frappe.only_for("Stock User")\n'


class TheOtherFiles(unittest.TestCase):
    def judged(self, text: str, path: str = "app/api.py", **files) -> list[str]:
        tree = check.Tree({"app/permitted.py": PERMITTED, "app/__init__.py": "", "app/sub/__init__.py": "", **files}.get)
        return [f.what for f in check.new_findings(path, text, None, now=tree)]

    def test_a_helper_imported_by_name(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom app.permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n"
        ), [])

    def test_a_helper_imported_under_another_name(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom app.permitted import require_staff as _guard\n\n"
            "@frappe.whitelist()\ndef read_note():\n    _guard()\n"
        ), [])

    def test_a_helper_of_an_imported_module(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom app import permitted\n\n"
            "@frappe.whitelist()\ndef read_note():\n    permitted.require_staff()\n"
        ), [])

    def test_a_helper_of_a_module_imported_whole(self):
        self.assertEqual(self.judged(
            "import frappe\nimport app.permitted\n\n"
            "@frappe.whitelist()\ndef read_note():\n    app.permitted.require_staff()\n"
        ), [])

    def test_a_relative_import(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom .permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n"
        ), [])

    def test_a_relative_import_one_package_up(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom ..permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n",
            path="app/sub/api.py",
        ), [])

    def test_a_relative_import_from_a_package_file(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom .permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n",
            path="app/__init__.py",
        ), [])

    def test_an_import_inside_the_method(self):
        self.assertEqual(self.judged(
            "import frappe\n\n"
            "@frappe.whitelist()\ndef read_note():\n"
            "    from app import permitted as p\n    p.require_staff()\n"
        ), [])

    def test_an_alias_of_a_function_of_another_module(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom app import permitted\n\n_require = permitted.require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    _require()\n"
        ), [])

    def test_a_helper_handed_on_by_a_package(self):
        self.assertEqual(self.judged(
            "import frappe\nfrom app.sub import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n",
            **{"app/sub/__init__.py": "from app.permitted import require_staff\n"},
        ), [])

    def test_a_file_that_is_not_there_is_asked_once(self):
        asked = []

        def read(path):
            asked.append(path)
            return None

        calls = "\n".join(f"    frappe.utils.helper_{n}()" for n in range(50))
        text = f"import frappe\n\n@frappe.whitelist()\ndef read_note():\n{calls}\n"
        check.new_findings("app/api.py", text, None, now=check.Tree(read))
        self.assertEqual(len(asked), len(set(asked)))
        self.assertLessEqual(len(asked), 2)

    def test_a_module_out_of_the_app_is_no_check(self):
        self.assertEqual(len(self.judged(
            "import frappe\nfrom erpnext.stock import require_staff\n\n"
            "@frappe.whitelist()\ndef read_note():\n    require_staff()\n"
        )), 1)


class TheGuests(unittest.TestCase):
    def test_a_new_guest_method_needs_a_reason(self):
        found = refused("""
            @frappe.whitelist(allow_guest=True)
            def public_hours():
                return {"open": 9}
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("guest", found[0])

    def test_even_when_it_holds_a_check(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist(allow_guest=True)
            def public_note(name: str):
                frappe.has_permission("Note", "read", name, throw=True)
        """)), 1)

    def test_a_reason_opens_it(self):
        self.assertEqual(refused("""
            @frappe.whitelist(allow_guest=True)
            def public_hours():  # permission-check: ok opening hours are printed on the door
                return {"open": 9}
        """), [])

    def test_a_value_that_is_not_false_is_a_guest(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist(allow_guest=ALLOW_GUEST)
            def public_hours():
                frappe.only_for("Stock User")
        """)), 1)

    def test_allow_guest_false_is_no_guest(self):
        self.assertEqual(refused("""
            @frappe.whitelist(allow_guest=False)
            def read_note():
                frappe.only_for("Stock User")
        """), [])

    def test_an_old_guest_method_that_changes_and_keeps_its_check_passes(self):
        before = """
            @frappe.whitelist(allow_guest=True)
            def public_note(name: str):
                frappe.has_permission("Note", "read", name, throw=True)
                return 1
        """
        after = """
            @frappe.whitelist(allow_guest=True)
            def public_note(name: str):
                frappe.has_permission("Note", "read", name, throw=True)
                return 2
        """
        self.assertEqual(refused(after, before), [])

    def test_an_old_guest_method_that_changes_with_no_check_is_refused(self):
        before = """
            @frappe.whitelist(allow_guest=True)
            def public_hours():
                return 1
        """
        after = """
            @frappe.whitelist(allow_guest=True)
            def public_hours():
                return 2
        """
        self.assertEqual(len(refused(after, before)), 1)

    def test_an_old_method_opened_to_guests_is_a_new_guest(self):
        before = """
            @frappe.whitelist()
            def read_note(name: str):
                frappe.has_permission("Note", "read", name, throw=True)
        """
        after = """
            @frappe.whitelist(allow_guest=True)
            def read_note(name: str):
                frappe.has_permission("Note", "read", name, throw=True)
        """
        self.assertEqual(len(refused(after, before)), 1)


class TheReasons(unittest.TestCase):
    def test_a_reason_on_the_def_line(self):
        self.assertEqual(refused("""
            @frappe.whitelist()
            def my_name():  # permission-check: ok the caller's own first name, nothing else
                return frappe.get_value("User", frappe.session.user, "first_name")
        """), [])

    def test_a_reason_on_the_last_line_of_a_long_signature(self):
        self.assertEqual(refused("""
            @frappe.whitelist()
            def my_name(
                first: str,
                second: str,
            ):  # permission-check: ok the caller's own first name, nothing else
                return 1
        """), [])

    def test_a_reason_on_the_first_line_of_a_long_signature(self):
        self.assertEqual(refused("""
            @frappe.whitelist()
            def my_name(  # permission-check: ok the caller's own first name, nothing else
                first: str,
            ):
                return 1
        """), [])

    def test_a_reason_on_the_line_above_the_decorators(self):
        self.assertEqual(refused("""
            # permission-check: ok the caller's own first name, nothing else
            @frappe.whitelist()
            def my_name():
                return 1
        """), [])

    def test_a_mark_with_no_reason_is_no_reason(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist()
            def my_name():  # permission-check: ok
                return 1
        """)), 1)

    def test_a_reason_too_short_is_no_reason(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist()
            def my_name():  # permission-check: ok fine
                return 1
        """)), 1)

    def test_a_reason_in_the_body_does_not_count(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist()
            def my_name():
                # permission-check: ok the caller's own first name, nothing else
                return 1
        """)), 1)

    def test_a_reason_on_another_method_does_not_count(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist()
            def first():  # permission-check: ok the caller's own first name, nothing else
                return 1

            @frappe.whitelist()
            def second():
                return 2
        """)), 1)


class WhatIsTouched(unittest.TestCase):
    OLD = """
        @frappe.whitelist()
        def read_note(name):
            return frappe.get_doc("Note", name)
    """

    def test_the_stock_blocks_nobody(self):
        self.assertEqual(refused(self.OLD, self.OLD), [])

    def test_another_method_next_to_the_stock_is_judged_alone(self):
        found = check.new_findings(
            "app/api.py",
            source(self.OLD) + "\n" + method("return 1").replace("read_note", "read_more"),
            source(self.OLD),
        )
        self.assertEqual([f.method for f in found], ["read_more"])

    def test_a_changed_body_is_judged(self):
        after = """
            @frappe.whitelist()
            def read_note(name):
                return frappe.get_doc("Note", name).as_dict()
        """
        self.assertEqual(len(refused(after, self.OLD)), 1)

    def test_a_changed_signature_is_judged(self):
        after = """
            @frappe.whitelist()
            def read_note(name, extra=None):
                return frappe.get_doc("Note", name)
        """
        self.assertEqual(len(refused(after, self.OLD)), 1)

    def test_a_function_that_becomes_a_web_method_is_judged(self):
        before = """
            def read_note(name):
                return frappe.get_doc("Note", name)
        """
        self.assertEqual(len(refused(self.OLD, before)), 1)

    def test_a_comment_changes_nothing(self):
        after = """
            @frappe.whitelist()
            def read_note(name):
                # the note, whole
                return frappe.get_doc("Note", name)
        """
        self.assertEqual(refused(after, self.OLD), [])

    def test_a_docstring_changes_nothing(self):
        after = '''
            @frappe.whitelist()
            def read_note(name):
                """The note, whole."""
                return frappe.get_doc("Note", name)
        '''
        self.assertEqual(refused(after, self.OLD), [])

    def test_the_layout_changes_nothing(self):
        after = """
            @frappe.whitelist()
            def read_note(
                name,
            ):
                return frappe.get_doc(
                    "Note",
                    name,
                )
        """
        self.assertEqual(refused(after, self.OLD), [])

    def test_a_method_no_longer_exposed_is_not_judged(self):
        after = """
            def read_note(name):
                return frappe.get_doc("Note", name)
        """
        self.assertEqual(refused(after, self.OLD), [])

    def test_a_helper_that_loses_its_check_refuses_the_method_that_relied_on_it(self):
        before = """
            def _require():
                frappe.only_for("Stock User")

            @frappe.whitelist()
            def read_note():
                _require()
                return 1
        """
        after = """
            def _require():
                pass

            @frappe.whitelist()
            def read_note():
                _require()
                return 1
        """
        self.assertEqual(len(refused(after, before)), 1)

    def test_a_method_moved_from_another_file_is_stock(self):
        moved = check.fingerprints(source(self.OLD))
        self.assertEqual(refused(self.OLD, moved=moved), [])

    def test_a_copy_under_another_name_is_new(self):
        moved = check.fingerprints(source(self.OLD))
        after = self.OLD.replace("read_note", "read_note_again")
        self.assertEqual(len(refused(after, moved=moved)), 1)


class TheFilters(unittest.TestCase):
    def found(self, body: str, parameters: str = "name") -> list[str]:
        lines = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
        text = (
            f"@frappe.whitelist()\ndef read_note({parameters}):\n"
            f'    frappe.only_for("Stock User")\n{lines}\n'
        )
        return refused(text)

    def test_a_bare_parameter_as_the_filter_of_get_value(self):
        found = self.found('return frappe.db.get_value("Note", name, "title")')
        self.assertEqual(len(found), 1)
        self.assertIn("'name'", found[0])
        self.assertIn("frappe.db.get_value", found[0])

    def test_every_call_that_filters(self):
        for line in (
            'frappe.db.get_value("Note", name, "title")',
            'frappe.db.get_value("Note", filters=name)',
            'frappe.get_value("Note", name, "title")',
            'frappe.db.get_values("Note", name, "title")',
            'frappe.db.exists("Note", name)',
            'frappe.db.exists("Note", dn=name)',
            "frappe.db.exists(name)",
            'frappe.get_all("Note", filters=name)',
            'frappe.get_all("Note", or_filters=name)',
            'frappe.get_all("Note", ["name"], name)',
            'frappe.db.get_all("Note", filters=name)',
        ):
            with self.subTest(line):
                self.assertEqual(len(self.found(line)), 1)

    def test_inside_a_filter_written_in_place(self):
        for line in (
            'frappe.get_all("Note", filters={"title": name})',
            'frappe.get_all("Note", filters=[["title", "=", name]])',
            'frappe.db.exists({"doctype": "Note", "title": name})',
            'frappe.db.get_value("Note", {"title": name} if name else None)',
        ):
            with self.subTest(line):
                self.assertEqual(len(self.found(line)), 1)

    def test_through_a_variable(self):
        self.assertEqual(len(self.found("""
            filters = {"title": name}
            return frappe.get_all("Note", filters=filters)
        """)), 1)

    def test_through_a_key_set_later(self):
        self.assertEqual(len(self.found("""
            filters = {}
            filters["title"] = name
            return frappe.get_all("Note", filters=filters)
        """)), 1)

    def test_through_an_update(self):
        self.assertEqual(len(self.found("""
            filters = {}
            filters.update({"title": name})
            return frappe.get_all("Note", filters=filters)
        """)), 1)

    def test_through_a_loop(self):
        self.assertEqual(len(self.found("""
            for one in name:
                frappe.db.exists("Note", one)
        """)), 1)

    def test_through_a_comprehension(self):
        self.assertEqual(len(self.found("""
            return [frappe.db.exists("Note", one) for one in name]
        """)), 1)

    def test_through_parse_json(self):
        self.assertEqual(len(self.found("""
            filters = frappe.parse_json(name)
            return frappe.get_all("Note", filters=filters)
        """)), 1)

    def test_through_a_key_of_a_parameter(self):
        self.assertEqual(len(self.found("""
            data = frappe.parse_json(name)
            return frappe.db.exists("Note", data.get("title"))
        """)), 1)

    def test_a_keyword_catch_all_is_a_parameter(self):
        self.assertEqual(len(self.found("""
            return frappe.db.exists("Note", kwargs.get("title"))
        """, "**kwargs")), 1)

    def test_a_parameter_with_a_default_is_a_parameter(self):
        self.assertEqual(len(self.found(
            'return frappe.db.get_value("Note", name, "title")', "name=None"
        )), 1)

    def test_an_annotation_settles_it(self):
        for parameters in ("name: str", "name: str = None", "name: dict | None = None"):
            with self.subTest(parameters):
                self.assertEqual(self.found('return frappe.db.get_value("Note", name, "title")', parameters), [])

    def test_a_value_turned_into_text_settles_it(self):
        for line in (
            'frappe.db.get_value("Note", str(name), "title")',
            'frappe.db.get_value("Note", cstr(name), "title")',
            'frappe.db.exists("Note", f"NOTE-{name}")',
            'frappe.get_all("Note", filters={"title": name.strip()})',
        ):
            with self.subTest(line):
                self.assertEqual(self.found(line), [])

    def test_a_variable_cleaned_on_the_way(self):
        self.assertEqual(self.found("""
            name = cstr(name)
            return frappe.db.get_value("Note", name, "title")
        """), [])

    def test_a_value_refused_unless_it_is_a_text(self):
        self.assertEqual(self.found("""
            if not isinstance(name, str):
                frappe.throw(_("Not permitted"), frappe.PermissionError)
            return frappe.db.get_value("Note", name, "title")
        """), [])

    def test_a_value_refused_by_a_return(self):
        self.assertEqual(self.found("""
            if not isinstance(name, (str, int)):
                return None
            return frappe.db.get_value("Note", name, "title")
        """), [])

    def test_a_list_allowed_by_the_type_test_is_still_a_filter(self):
        self.assertEqual(len(self.found("""
            if not isinstance(name, (str, list)):
                return None
            return frappe.db.get_value("Note", name, "title")
        """)), 1)

    def test_a_type_test_that_stops_nothing_settles_nothing(self):
        self.assertEqual(len(self.found("""
            if not isinstance(name, str):
                log(name)
            return frappe.db.get_value("Note", name, "title")
        """)), 1)

    def test_a_text_parsed_into_a_filter_is_still_a_filter(self):
        self.assertEqual(len(self.found("""
            if isinstance(name, str):
                name = frappe.parse_json(name)
            return frappe.get_all("Note", filters=name)
        """)), 1)

    def test_a_helper_that_refuses_anything_but_a_text(self):
        self.assertEqual(refused("""
            def _require_name(value):
                if not isinstance(value, str):
                    frappe.throw(_("Not permitted"), frappe.PermissionError)

            @frappe.whitelist()
            def read_note(name):
                frappe.only_for("Stock User")
                _require_name(name)
                return frappe.db.get_value("Note", name, "title")
        """), [])

    def test_a_helper_called_after_the_query_settles_nothing(self):
        self.assertEqual(len(refused("""
            def _require_name(value):
                if not isinstance(value, str):
                    frappe.throw(_("Not permitted"), frappe.PermissionError)

            @frappe.whitelist()
            def read_note(name):
                frappe.only_for("Stock User")
                title = frappe.db.get_value("Note", name, "title")
                _require_name(name)
                return title
        """)), 1)

    def test_a_helper_that_tests_another_argument_settles_nothing(self):
        self.assertEqual(len(refused("""
            def _require_name(other, value):
                if not isinstance(value, str):
                    frappe.throw(_("Not permitted"), frappe.PermissionError)

            @frappe.whitelist()
            def read_note(name):
                frappe.only_for("Stock User")
                _require_name(name, "x")
                return frappe.db.get_value("Note", name, "title")
        """)), 1)

    def test_the_doctype_position_is_no_filter(self):
        self.assertEqual(self.found('return frappe.db.get_value(name, "NOTE-1", "title")'), [])

    def test_the_fields_position_is_no_filter(self):
        self.assertEqual(self.found('return frappe.get_all("Note", fields=name)'), [])
        self.assertEqual(self.found('return frappe.get_all("Note", name)'), [])

    def test_another_query_is_no_filter(self):
        self.assertEqual(self.found('return frappe.get_list("Note", filters={"title": name})'), [])

    def test_a_reason_does_not_excuse_it(self):
        found = refused("""
            @frappe.whitelist(allow_guest=True)
            def public_note(name):  # permission-check: ok public notes are printed on the door
                return frappe.db.get_value("Note", name, "title")
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("'name'", found[0])

    def test_both_defects_of_one_method_are_said(self):
        self.assertEqual(len(refused("""
            @frappe.whitelist()
            def read_note(name):
                return frappe.db.get_value("Note", name, "title")
        """)), 2)

    def test_two_parameters_are_said_one_by_one(self):
        self.assertEqual(len(self.found("""
            frappe.db.exists("Note", first)
            frappe.db.exists("Note", second)
        """, "first, second")), 2)

    def test_self_is_no_parameter(self):
        self.assertEqual(refused("""
            class Note(Document):
                @frappe.whitelist()
                def siblings(self):
                    return frappe.get_all("Note", filters={"parent_note": self})
        """), [])

    def test_a_method_of_a_document_is_judged_too(self):
        self.assertEqual(len(refused("""
            class Note(Document):
                @frappe.whitelist()
                def sibling(self, title):
                    return frappe.db.exists("Note", {"title": title})
        """)), 1)

    def test_an_untouched_method_is_left_alone(self):
        old = """
            @frappe.whitelist()
            def read_note(name):
                frappe.only_for("Stock User")
                return frappe.db.get_value("Note", name, "title")
        """
        self.assertEqual(refused(old, old), [])


class TheRepository(unittest.TestCase):
    OLD = 'import frappe\n\n@frappe.whitelist()\ndef old_method(name):\n    return frappe.get_doc("Note", name)\n'
    NEW = 'import frappe\n\n@frappe.whitelist()\ndef new_method(name: str):\n    return frappe.get_doc("Note", name)\n'

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = self.folder.name
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.com")
        self.git("config", "user.name", "ci")
        self.write("shop/__init__.py", "")
        self.write("shop/old.py", self.OLD)
        self.write("shop/permitted.py", 'import frappe\n\ndef require_staff():\n    frappe.only_for("Stock User")\n')
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *arguments):
        subprocess.run(["git", "-C", self.root, *arguments], check=True, capture_output=True)

    def write(self, name, text):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message="next"):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def script(self, *arguments, **env_more):
        env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
        env.update(env_more)
        script = os.path.abspath(check.__file__)
        return subprocess.run(
            [sys.executable, script, "--root", self.root, *arguments], capture_output=True, text=True, env=env
        )

    def test_nothing_changed_nothing_refused(self):
        self.assertEqual(self.script("--base", "HEAD").returncode, 0)

    def test_a_new_method_is_refused_against_the_base(self):
        self.write("shop/new.py", self.NEW)
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("shop/new.py:4", done.stdout)
        self.assertIn("new_method", done.stdout)
        self.assertIn("permission-check: ok", done.stderr)

    def test_github_gets_an_annotation(self):
        self.write("shop/new.py", self.NEW)
        self.commit()
        done = self.script("--base", "HEAD~1", GITHUB_ACTIONS="true")
        self.assertIn("::error file=shop/new.py,line=4::", done.stdout)

    def test_the_old_method_stays_green(self):
        self.write("README", "x")
        self.write("shop/old.py", self.OLD + "\ndef helper():\n    return 1\n")
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_helper_of_another_file_is_read_from_the_commit_judged(self):
        self.write(
            "shop/new.py",
            "import frappe\nfrom shop.permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef new_method():\n    require_staff()\n",
        )
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_file_renamed_whole_is_stock(self):
        self.git("mv", "shop/old.py", "shop/renamed.py")
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_method_moved_to_another_file_is_stock(self):
        self.write("shop/old.py", "import frappe\n")
        self.write("shop/other.py", self.OLD)
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_copy_that_leaves_the_original_in_place_is_new(self):
        self.write("shop/other.py", self.OLD)
        self.commit()
        self.assertEqual(self.script("--base", "HEAD~1").returncode, 1)

    def test_the_tests_of_an_app_are_not_judged(self):
        self.write("shop/test_new.py", self.NEW)
        self.write("shop/tests/helpers.py", self.NEW)
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_merge_of_upstream_is_not_judged(self):
        self.git("checkout", "-q", "-b", "upstream")
        self.write("shop/theirs.py", self.NEW)
        self.commit("theirs")
        self.git("checkout", "-q", "-")
        self.write("README", "ours")
        self.commit("ours")
        self.git("merge", "-q", "--no-edit", "upstream")
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("merge", done.stdout)

    def test_staged(self):
        self.write("shop/new.py", self.NEW)
        self.git("add", "shop/new.py")
        self.assertEqual(self.script("--staged").returncode, 1)

    def test_staged_reads_the_index_and_not_the_disk(self):
        self.write("shop/new.py", self.NEW)
        self.git("add", "shop/new.py")
        self.write("shop/new.py", "import frappe\n")
        self.assertEqual(self.script("--staged").returncode, 1)

    def test_a_file_on_disk_against_head(self):
        self.write("shop/new.py", self.NEW)
        self.assertEqual(self.script(os.path.join(self.root, "shop/new.py")).returncode, 1)

    def test_a_file_that_is_not_python_is_not_read(self):
        self.write("shop/notes.txt", "x")
        done = self.script(os.path.join(self.root, "shop/notes.txt"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_file_moved_on_disk_is_stock(self):
        self.write("shop/old.py", "import frappe\n")
        self.write("shop/other.py", self.OLD)
        done = self.script(os.path.join(self.root, "shop/other.py"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_base_is_required(self):
        self.assertEqual(self.script().returncode, 2)

    def test_the_inventory_refuses_nothing(self):
        done = self.script("--inventory")
        self.assertEqual(done.returncode, 0)
        self.assertIn("none", done.stdout)

    def test_the_inventory_says_what_each_method_holds(self):
        self.write(
            "shop/new.py",
            "import frappe\nfrom shop.permitted import require_staff\n\n"
            "@frappe.whitelist()\ndef checked():\n    frappe.only_for('Stock User')\n\n"
            "@frappe.whitelist()\ndef helped():\n    require_staff()\n\n"
            "@frappe.whitelist(allow_guest=True)\n"
            "def opened():  # permission-check: ok opening hours are printed on the door\n    return 1\n\n"
            "class Note(Document):\n    @frappe.whitelist()\n    def archive(self):\n        return 1\n",
        )
        rows = json.loads(self.script("--inventory", "--json").stdout)
        by_name = {row["method"]: row for row in rows}
        self.assertEqual(by_name["old_method"]["kind"], "none")
        self.assertEqual(by_name["checked"]["kind"], "direct")
        self.assertEqual(by_name["helped"]["kind"], "helper")
        self.assertIn("require_staff", by_name["helped"]["control"])
        self.assertEqual(by_name["Note.archive"]["kind"], "document")
        self.assertTrue(by_name["opened"]["guest"])
        self.assertTrue(by_name["opened"]["reasoned"])
        self.assertEqual(by_name["old_method"]["file"], "shop/old.py")

    def test_a_module_and_a_package_of_one_name_are_two_files(self):
        self.write("shop/twin.py", "import frappe\n\n@frappe.whitelist()\ndef in_the_module():\n    return 1\n")
        self.write("shop/twin/__init__.py", "import frappe\n\n@frappe.whitelist()\ndef in_the_package():\n    return 1\n")
        rows = json.loads(self.script("--inventory", "--json").stdout)
        found = sorted((row["file"], row["method"]) for row in rows if "twin" in row["file"])
        self.assertEqual(found, [("shop/twin.py", "in_the_module"), ("shop/twin/__init__.py", "in_the_package")])

    def test_of_a_module_and_a_package_python_imports_the_package(self):
        self.write("shop/twin.py", "def guard():\n    pass\n")
        self.write("shop/twin/__init__.py", 'import frappe\n\ndef guard():\n    frappe.only_for("Stock User")\n')
        self.write(
            "shop/new.py",
            "import frappe\nfrom shop.twin import guard\n\n@frappe.whitelist()\ndef new_method():\n    guard()\n",
        )
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_inventory_gives_the_filters(self):
        self.write(
            "shop/new.py",
            'import frappe\n\n@frappe.whitelist()\ndef reads(name):\n    return frappe.db.exists("Note", name)\n',
        )
        rows = json.loads(self.script("--inventory", "--json").stdout)
        row = next(row for row in rows if row["method"] == "reads")
        self.assertEqual(row["filters"], [{"parameter": "name", "call": "frappe.db.exists", "line": 5}])


if __name__ == "__main__":
    unittest.main()
