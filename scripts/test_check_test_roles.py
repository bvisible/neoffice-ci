#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_test_roles as roles  # noqa: E402

HEAD = "import frappe\nfrom frappe.tests.roles import as_role\n\nclass TestShop:\n"


def source(body: str, head: str = HEAD) -> str:
    return head + textwrap.indent(textwrap.dedent(body).strip("\n"), "    ") + "\n"


def refused(body: str, base: str | None = None, **kwargs) -> list[str]:
    return [f.what for f in roles.new_findings("test_x.py", source(body), base and source(base), **kwargs)]


class TheRule(unittest.TestCase):
    def test_saying_nothing_is_acting_as_administrator(self):
        found = refused("""
            def test_order(self):
                frappe.get_doc({"doctype": "Sales Order", "customer": "x"}).insert()
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("Sales Order", found[0])
        self.assertIn("as Administrator", found[0])

    def test_every_business_doctype_of_the_issue_is_an_act(self):
        for doctype in (
            "Sales Order", "Sales Invoice", "Quotation", "Payment Entry", "Payment Request",
            "Purchase Order", "Purchase Invoice", "Purchase Receipt", "Stock Entry",
        ):
            found = refused(f"""
                def test_it(self):
                    doc = frappe.new_doc("{doctype}")
                    doc.submit()
            """)
            self.assertEqual(len(found), 1, doctype)

    def test_a_document_nobody_cares_about_is_no_act(self):
        self.assertEqual(refused("""
            def test_note(self):
                frappe.get_doc({"doctype": "Note", "title": "x"}).insert()
        """), [])

    def test_a_document_nobody_cares_about_through_a_dictionary_is_no_act(self):
        self.assertEqual(refused("""
            def test_note(self):
                values = {"doctype": "Employee", "first_name": "x"}
                doc = frappe.get_doc(values)
                doc.insert()
        """), [])

    def test_a_business_document_through_a_dictionary_is_an_act(self):
        self.assertEqual(len(refused("""
            def test_order(self):
                values = {"doctype": "Sales Order"}
                doc = frappe.get_doc(values)
                doc.insert()
        """)), 1)

    def test_a_read_is_no_act(self):
        self.assertEqual(refused("""
            def test_read(self):
                doc = frappe.get_doc("Sales Order", "SO-1")
                self.assertEqual(doc.status, "Draft")
        """), [])

    def test_as_role_is_a_real_act(self):
        self.assertEqual(refused("""
            def test_order(self):
                with as_role("sales_user"):
                    frappe.get_doc({"doctype": "Sales Order"}).insert()
        """), [])

    def test_set_user_is_a_real_act(self):
        self.assertEqual(refused("""
            def test_order(self):
                frappe.set_user("seller@yopmail.com")
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_frappe_test_case_set_user_is_a_real_act(self):
        self.assertEqual(refused("""
            def test_order(self):
                with self.set_user("seller@yopmail.com"):
                    frappe.new_doc("Sales Order").insert()
        """), [])

    def test_set_user_to_a_constant_naming_administrator_is_still_administrator(self):
        head = 'import frappe\nME = "Administrator"\n\nclass TestShop:\n'
        found = roles.new_findings("test_x.py", source("""
            def test_order(self):
                frappe.set_user(ME)
                frappe.new_doc("Sales Order").insert()
        """, head), None)
        self.assertEqual(len(found), 1)

    def test_a_real_user_in_passing_does_not_make_what_follows_scenery(self):
        found = refused("""
            def test_order(self):
                with as_role("sales_user"):
                    pass
                frappe.new_doc("Sales Order").insert()
        """)
        self.assertEqual(len(found), 1)

    def test_the_same_through_a_helper(self):
        found = refused("""
            def _order(self):
                frappe.new_doc("Sales Order").insert()

            def test_order(self):
                with as_role("sales_user"):
                    pass
                self._order()
        """)
        self.assertEqual(len(found), 1)

    def test_a_context_of_the_file_is_recognised_by_what_it_does(self):
        head = (
            "import frappe\nfrom contextlib import contextmanager\n\n@contextmanager\ndef _for(user):\n"
            "    frappe.set_user(user)\n    try:\n        yield\n    finally:\n"
            '        frappe.set_user("Administrator")\n\nclass TestShop:\n'
        )
        self.assertEqual(roles.new_findings("test_x.py", source("""
            def test_order(self):
                with _for("seller@yopmail.com"):
                    frappe.new_doc("Sales Order").insert()
        """, head), None), [])

    def test_a_context_named_for_a_person_is_a_real_act(self):
        for context in ("_as", "as_cashier", "signed_in", "logged_in"):
            self.assertEqual(refused(f"""
                def test_order(self):
                    with {context}("seller@yopmail.com"):
                        frappe.new_doc("Sales Order").insert()
            """), [], context)

    def test_a_helper_with_a_context_on_a_parameter_is_a_real_act(self):
        head = (
            "import frappe\nfrom contextlib import contextmanager\nCASHIER = 'x@yopmail.com'\n\n@contextmanager\n"
            "def _as(user):\n    frappe.set_user(user)\n    yield\n\nclass TestShop:\n"
        )
        self.assertEqual(roles.new_findings("test_x.py", source("""
            def _record(self, user=CASHIER):
                with _as(user):
                    frappe.new_doc("Journal Entry").insert()

            def test_order(self):
                self._record()
        """, head), None), [])

    def test_enter_context_hands_a_whole_class_over(self):
        self.assertEqual(refused("""
            def setUp(self):
                self.enterContext(as_role("sales_user"))

            def test_order(self):
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_a_session_set_by_hand_is_a_real_act(self):
        self.assertEqual(refused("""
            def test_order(self):
                frappe.session.user = "seller@yopmail.com"
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_the_scenery_may_come_between_two_steps_of_a_real_user(self):
        self.assertEqual(refused("""
            def test_order(self):
                with as_role("portal_customer"):
                    cart()
                frappe.new_doc("Payment Entry").submit()
                with as_role("portal_customer"):
                    see_it_paid()
        """), [])

    def test_an_administrator_context_in_a_class_of_a_real_user_is_scenery(self):
        self.assertEqual(refused("""
            def setUp(self):
                self.enterContext(as_role("sales_user"))

            def test_order(self):
                with as_administrator():
                    frappe.new_doc("Sales Order").insert()
                see_it()
        """), [])

    def test_as_role_with_administrator_is_no_real_user(self):
        found = refused("""
            def test_order(self):
                with as_role("Administrator"):
                    frappe.new_doc("Sales Order").insert()
        """)
        self.assertEqual(len(found), 1)

    def test_administrator_prepares_and_a_real_user_takes_over(self):
        self.assertEqual(refused("""
            def test_pay(self):
                order = frappe.new_doc("Sales Order")
                order.insert()
                order.submit()
                with as_role("portal_customer"):
                    pay(order.name)
        """), [])

    def test_the_scenery_of_set_up_is_not_an_act(self):
        self.assertEqual(refused("""
            def setUp(self):
                frappe.new_doc("Sales Order").insert()

            def test_read(self):
                pass
        """), [])

    def test_set_up_handing_over_makes_the_whole_class_real(self):
        self.assertEqual(refused("""
            def setUp(self):
                frappe.set_user("seller@yopmail.com")

            def test_order(self):
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_ignore_permissions_on_a_real_user_skips_the_check_under_test(self):
        found = refused("""
            def test_order(self):
                with as_role("sales_user"):
                    frappe.get_doc({"doctype": "Sales Order"}).insert(ignore_permissions=True)
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("ignore_permissions", found[0])

    def test_the_flag_is_ignore_permissions_too(self):
        found = refused("""
            def test_order(self):
                with as_role("sales_user"):
                    doc = frappe.new_doc("Sales Order")
                    doc.flags.ignore_permissions = True
                    doc.insert()
        """)
        self.assertEqual(len(found), 1)

    def test_ignore_permissions_in_the_scenery_is_fine(self):
        self.assertEqual(refused("""
            def test_pay(self):
                frappe.new_doc("Sales Order").insert(ignore_permissions=True)
                with as_role("portal_customer"):
                    pay()
        """), [])

    def test_a_helper_of_the_file_is_followed(self):
        found = refused("""
            def _order(self):
                return frappe.get_doc({"doctype": "Sales Order"}).insert()

            def test_order(self):
                self._order()
        """)
        self.assertEqual(len(found), 1)
        self.assertIn("_order", found[0])

    def test_a_helper_called_as_a_real_user_acts_as_that_user(self):
        self.assertEqual(refused("""
            def _order(self):
                return frappe.get_doc({"doctype": "Sales Order"}).insert()

            def test_order(self):
                with as_role("sales_user"):
                    self._order()
        """), [])

    def test_a_helper_that_logs_in_hands_over(self):
        self.assertEqual(refused("""
            def _login(self):
                frappe.set_user("seller@yopmail.com")

            def test_order(self):
                self._login()
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_the_helpers_of_erpnext_are_acts(self):
        found = refused("""
            def test_invoice(self):
                make_sales_invoice(qty=1)
        """)
        self.assertEqual(len(found), 1)

    def test_a_getter_is_no_act_until_it_is_saved(self):
        self.assertEqual(refused("""
            def test_read(self):
                get_payment_entry("Sales Invoice", "x")
        """), [])
        self.assertEqual(len(refused("""
            def test_pay(self):
                entry = get_payment_entry("Sales Invoice", "x")
                entry.submit()
        """)), 1)

    def test_a_function_of_the_app_is_an_act(self):
        head = "import frappe\nfrom shop.cart import place_order\n\nclass TestShop:\n"
        found = roles.new_findings(
            "test_x.py",
            source("""
                def test_place(self):
                    place_order()
            """, head),
            None,
            {"place_order": {"Sales Order"}},
            {"shop"},
        )
        self.assertEqual(len(found), 1)
        self.assertIn("place_order", found[0].what)

    def test_a_method_named_like_a_function_of_the_app_is_not(self):
        self.assertEqual(refused("""
            def test_it(self):
                {}.place_order()
        """, functions={"place_order": {"Sales Order"}}, packages={"shop"}), [])

    def test_a_test_that_patches_the_documents_is_not_judged_on_what_it_calls(self):
        head = "import frappe\nfrom unittest.mock import patch\nfrom shop.cart import place_order\n\nclass TestShop:\n"
        found = roles.new_findings(
            "test_x.py",
            source("""
                @patch("shop.cart.frappe.get_doc")
                def test_place(self, get_doc):
                    place_order()
            """, head),
            None,
            {"place_order": {"Sales Order"}},
            {"shop"},
        )
        self.assertEqual(found, [])


class TheExceptions(unittest.TestCase):
    def test_a_reason_on_the_def_line(self):
        self.assertEqual(refused("""
            def test_order(self):  # role-check: ok the nightly job runs as Administrator
                frappe.new_doc("Sales Order").insert()
        """), [])

    def test_a_reason_on_the_act(self):
        self.assertEqual(refused("""
            def test_order(self):
                frappe.new_doc("Sales Order").insert()  # role-check: ok the nightly job runs as Administrator
        """), [])

    def test_a_reason_for_a_whole_class(self):
        head = "import frappe\n\nclass TestShop:  # role-check: ok the scheduler runs these as Administrator\n"
        self.assertEqual(roles.new_findings("test_x.py", source("""
            def test_order(self):
                frappe.new_doc("Sales Order").insert()
        """, head), None), [])

    def test_no_reason_no_exception(self):
        self.assertEqual(len(refused("""
            def test_order(self):  # role-check: ok
                frappe.new_doc("Sales Order").insert()
        """)), 1)


class WhatIsNew(unittest.TestCase):
    OLD = """
        def test_order(self):
            frappe.new_doc("Sales Order").insert()
    """

    def test_an_existing_test_is_left_alone(self):
        self.assertEqual(refused(self.OLD, base=self.OLD), [])

    def test_a_new_test_beside_it_is_judged(self):
        found = refused(self.OLD + """
        def test_invoice(self):
            frappe.new_doc("Sales Invoice").insert()
        """, base=self.OLD)
        self.assertEqual(len(found), 1)
        self.assertIn("Sales Invoice", found[0])

    def test_a_new_act_in_an_existing_test_is_judged(self):
        found = refused("""
            def test_order(self):
                frappe.new_doc("Sales Order").insert()
                frappe.new_doc("Payment Entry").submit()
        """, base=self.OLD)
        self.assertEqual(len(found), 1)
        self.assertIn("Payment Entry", found[0])

    def test_a_syntax_error_is_the_test_runs_business(self):
        self.assertEqual(roles.new_findings("test_x.py", "def (", None), [])


class TheRepository(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = self.folder.name
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.com")
        self.git("config", "user.name", "ci")
        self.write("test_old.py", 'import frappe\ndef test_old():\n    frappe.new_doc("Sales Order").insert()\n')
        self.write("shop/__init__.py", "")
        self.write(
            "shop/cart.py",
            'import frappe\ndef _make():\n    frappe.new_doc("Sales Order").insert()\ndef place_order():\n    _make()\n',
        )
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *arguments):
        subprocess.run(["git", "-C", self.root, *arguments], check=True, capture_output=True)

    def write(self, name, text):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def script(self, *arguments):
        env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
        script = os.path.abspath(roles.__file__)
        return subprocess.run(
            [sys.executable, script, "--root", self.root, *arguments], capture_output=True, text=True, env=env
        )

    def test_the_function_of_the_app_is_followed_through_the_file(self):
        self.assertEqual(roles.app_functions(self.root, {"shop"}).get("place_order"), {"Sales Order"})

    def test_a_call_is_followed_two_files_away_and_no_further(self):
        self.write("shop/four.py", 'import frappe\ndef four():\n    frappe.new_doc("Sales Order").insert()\n')
        self.write("shop/three.py", "from shop.four import four\ndef three():\n    four()\n")
        self.write("shop/two.py", "from shop.three import three\ndef two():\n    three()\n")
        self.write("shop/one.py", "from shop.two import two\ndef one():\n    two()\n")
        found = roles.app_functions(self.root, {"shop"})
        self.assertEqual(found.get("two"), {"Sales Order"})
        self.assertNotIn("one", found)

    def test_nothing_changed_nothing_refused(self):
        self.assertEqual(self.script("--base", "HEAD").returncode, 0)

    def test_a_new_file_is_refused_against_the_base(self):
        self.write("test_new.py", 'import frappe\nfrom shop.cart import place_order\ndef test_new():\n    place_order()\n')
        self.git("add", ".")
        self.git("commit", "-qm", "new")
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("test_new.py", done.stdout)

    def test_the_old_test_stays_green(self):
        self.write("README", "x")
        self.git("add", ".")
        self.git("commit", "-qm", "other")
        self.assertEqual(self.script("--base", "HEAD~1").returncode, 0)

    def test_staged(self):
        self.write("test_new.py", 'import frappe\ndef test_new():\n    frappe.new_doc("Sales Order").insert()\n')
        self.git("add", "test_new.py")
        self.assertEqual(self.script("--staged").returncode, 1)

    def test_a_file_on_disk_against_head(self):
        self.write("test_new.py", 'import frappe\ndef test_new():\n    frappe.new_doc("Sales Order").insert()\n')
        path = os.path.join(self.root, "test_new.py")
        self.assertEqual(self.script(path).returncode, 1)

    def test_the_inventory_refuses_nothing(self):
        done = self.script("--inventory")
        self.assertEqual(done.returncode, 0)
        self.assertIn("admin-act", done.stdout)

    def test_a_base_is_required(self):
        self.assertEqual(self.script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
