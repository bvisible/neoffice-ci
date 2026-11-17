#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_dialogs as dialogs  # noqa: E402

JS = "app/public/js/x.js"
CSS = "app/public/css/x.css"

FIVE_FIELDS = """
    {fieldname: "a", fieldtype: "Data", label: "A"},
    {fieldname: "b", fieldtype: "Link", options: "Customer"},
    {fieldname: "c", fieldtype: "Date"},
    {fieldname: "d", fieldtype: "Check"},
    {fieldname: "e", fieldtype: "Small Text"},
"""


def dialog(options: str, before: str = "", after: str = "") -> str:
    return textwrap.dedent(before) + "const d = new frappe.ui.Dialog({\n" + textwrap.dedent(options) + "});\n" + (
        textwrap.dedent(after)
    )


def found(text: str, base: str | None = None, path: str = JS, aliases=frozenset()) -> list:
    return dialogs.new_findings(path, text, base, set(aliases))


def rules(text: str, base: str | None = None, path: str = JS, aliases=frozenset()) -> list[str]:
    return [f.rule for f in found(text, base, path, aliases)]


class TheSize(unittest.TestCase):
    def test_five_fields_and_no_size_is_refused(self):
        refused = found(dialog(f'title: __("New customer"),\nfields: [{FIVE_FIELDS}],\n'
                               'primary_action_label: __("Create the customer"),\nprimary_action() {},\n'))
        self.assertEqual([f.rule for f in refused], ["size-missing"])
        self.assertIn('"large"', refused[0].what)
        self.assertEqual(refused[0].line, 1)
        self.assertEqual(refused[0].title, "New customer")

    def test_five_fields_in_size_large_pass(self):
        self.assertEqual(rules(dialog(f'size: "large",\nfields: [{FIVE_FIELDS}],\n')), [])

    def test_extra_large_passes(self):
        self.assertEqual(rules(dialog(f"size: 'extra-large',\nfields: [{FIVE_FIELDS}],\n")), [])

    def test_four_fields_need_no_size(self):
        four = FIVE_FIELDS.strip().rsplit("\n", 1)[0]
        self.assertEqual(rules(dialog(f"fields: [{four}],\n")), [])

    def test_html_headings_and_breaks_are_not_fields(self):
        self.assertEqual(rules(dialog("""
            fields: [
                {fieldtype: "HTML", fieldname: "intro"},
                {fieldtype: "Data", fieldname: "a"},
                {fieldtype: "Section Break"},
                {fieldtype: "Data", fieldname: "b"},
                {fieldtype: "Heading", label: "x"},
                {fieldtype: "Button", fieldname: "go"},
                {fieldtype: "Data", fieldname: "c"},
            ],
        """)), [])

    def test_a_hidden_field_does_not_count(self):
        fields = FIVE_FIELDS.replace('"Check"}', '"Check", hidden: true}')
        fields += '{fieldname: "f", fieldtype: "Data", hidden: 1},'
        self.assertEqual(rules(dialog(f"fields: [{fields}],\n")), [])
        shown = FIVE_FIELDS.replace('"Check"}', '"Check", hidden: 0}')
        self.assertEqual(rules(dialog(f"fields: [{shown}],\n")), ["size-missing"])

    def test_a_table_counts_once(self):
        self.assertEqual(rules(dialog("""
            fields: [
                {fieldname: "items", fieldtype: "Table", fields: [
                    {fieldname: "a", fieldtype: "Data", in_list_view: 1},
                    {fieldname: "b", fieldtype: "Data", in_list_view: 1},
                    {fieldname: "c", fieldtype: "Data", in_list_view: 1},
                    {fieldname: "d", fieldtype: "Data", in_list_view: 1},
                    {fieldname: "e", fieldtype: "Data", in_list_view: 1},
                ]},
            ],
        """)), [])

    def test_a_size_that_is_not_of_the_model_is_refused(self):
        for size in ("medium", "xl", "modal-lg"):
            refused = found(dialog(f'size: "{size}",\nfields: [{{fieldtype: "Data", fieldname: "a"}}],\n'))
            self.assertEqual([f.rule for f in refused], ["size-missing"], size)
            self.assertIn(size, refused[0].what)

    def test_small_is_not_the_size_of_a_form(self):
        refused = found(dialog(f'size: "small",\nfields: [{FIVE_FIELDS}],\n'))
        self.assertEqual([f.rule for f in refused], ["size-missing"])
        self.assertIn('"small"', refused[0].what)

    def test_a_size_by_a_condition_is_read_for_its_values(self):
        self.assertEqual(rules(dialog(f'size: wide ? "extra-large" : "large",\nfields: [{FIVE_FIELDS}],\n')), [])
        self.assertEqual(rules(dialog('size: wide ? "xl" : "large",\nfields: [],\n')), ["size-missing"])

    def test_frappe_widens_two_column_breaks_by_itself(self):
        self.assertEqual(rules(dialog("""
            fields: [
                {fieldtype: "Data", fieldname: "a"}, {fieldtype: "Data", fieldname: "b"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "c"}, {fieldtype: "Data", fieldname: "d"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "e"},
            ],
        """)), [])

    def test_fields_of_a_variable_declared_before_are_read(self):
        before = f"const fields = [{FIVE_FIELDS}];\n"
        self.assertEqual(rules(dialog("title: 'x',\nfields: fields,\n", before)), ["size-missing"])
        self.assertEqual(rules(dialog("title: 'x',\nfields,\n", before)), ["size-missing"])
        self.assertEqual(rules(dialog("size: 'large',\nfields,\n", before)), [])

    def test_fields_that_cannot_be_read_are_not_judged(self):
        self.assertEqual(rules(dialog("fields: get_fields(),\n")), [])
        self.assertEqual(rules(dialog("fields: [...common, {fieldtype: 'Data', fieldname: 'x'}],\n")), [])

    def test_options_that_spread_another_object_may_hold_the_size(self):
        self.assertEqual(rules(dialog(f"...defaults,\nfields: [{FIVE_FIELDS}],\n")), [])

    def test_options_given_by_a_variable_are_not_judged(self):
        self.assertEqual(rules("const d = new frappe.ui.Dialog(options);\n"), [])

    def test_a_multi_select_dialog_is_judged_for_its_width_only(self):
        text = "new frappe.ui.form.MultiSelectDialog({\n" + f"fields: [{FIVE_FIELDS}],\n" + "});\n"
        self.assertEqual(rules(text), [])


class TheVerb(unittest.TestCase):
    ONE = 'fields: [{fieldtype: "Data", fieldname: "name"}],\n'

    def test_submit_is_refused(self):
        refused = found(dialog(self.ONE + 'primary_action_label: __("Submit"),\nprimary_action: () => {},\n'))
        self.assertEqual([f.rule for f in refused], ["vague-verb"])
        self.assertIn("Submit", refused[0].what)
        self.assertEqual(refused[0].line, 3)

    def test_every_vague_word_in_any_case_and_any_quote(self):
        for label in ("'Valider'", '"VALIDER"', "__('Confirm')", "`Confirmer`", "__(\"Oui\")", "'yes'", "'Non'",
                      "'No'", '__("Ok")'):
            self.assertEqual(rules(dialog(self.ONE + f"primary_action_label: {label},\nprimary_action() {{}},\n")),
                             ["vague-verb"], label)

    def test_a_verb_passes(self):
        for label in ('__("Save")', '__("Create the task")', '__("Submit and continue")', '__("Archive")'):
            self.assertEqual(rules(dialog(self.ONE + f"primary_action_label: {label},\n")), [], label)

    def test_ok_is_the_action_of_a_message(self):
        self.assertEqual(rules(dialog('fields: [{fieldtype: "HTML", fieldname: "m"}],\n'
                                      'primary_action_label: __("OK"),\nprimary_action: () => d.hide(),\n')), [])
        self.assertEqual(rules(dialog('title: __("The invoice is sent"),\nprimary_action_label: "OK",\n')), [])

    def test_ok_on_a_window_that_takes_input_is_refused(self):
        refused = found(dialog(self.ONE + 'primary_action_label: __("OK"),\n'))
        self.assertEqual([f.rule for f in refused], ["vague-verb"])
        self.assertIn("message", refused[0].what)

    def test_ok_on_a_reading_of_read_only_fields_is_a_message(self):
        self.assertEqual(rules(dialog('fields: [{fieldtype: "Data", fieldname: "a", read_only: 1}],\n'
                                      'primary_action_label: "OK",\n')), [])

    def test_ok_on_fields_that_cannot_be_read_is_not_refused(self):
        self.assertEqual(rules(dialog('fields: build(),\nprimary_action_label: "OK",\n')), [])

    def test_submit_on_a_message_is_still_refused(self):
        self.assertEqual(rules(dialog('primary_action_label: __("Submit"),\n')), ["vague-verb"])

    def test_an_action_with_no_label_on_a_window_that_takes_input(self):
        refused = found(dialog(self.ONE + "primary_action(values) {\n    save(values);\n},\n"))
        self.assertEqual([f.rule for f in refused], ["vague-verb"])
        self.assertIn("Submit", refused[0].what)

    def test_an_action_with_no_label_on_a_message_is_not_refused(self):
        self.assertEqual(rules(dialog('fields: [{fieldtype: "HTML", fieldname: "m"}],\nprimary_action() {},\n')), [])

    def test_a_label_given_after_the_window_counts(self):
        after = 'd.set_primary_action(__("Create the task"), () => {});\n'
        self.assertEqual(rules(dialog(self.ONE + "primary_action() {},\n", after=after)), [])

    def test_a_wizard_that_names_its_action_from_its_step_function(self):
        after = "\n" * (dialogs.FOLLOW_LINES + 5) + "function goto_step(dialog, step) {\n"
        after += '  dialog.get_primary_btn().text(step < 3 ? __("Next") : __("Create"));\n}\n'
        text = dialog(self.ONE + "primary_action() {},\n", after=after).replace("const d =", "const dialog =")
        self.assertEqual(rules(text), [])

    def test_a_vague_label_given_after_the_window_is_refused(self):
        after = "\n\nd.set_primary_action(__('Submit'), () => d.hide());\n"
        refused = found(dialog(self.ONE, after=after))
        self.assertEqual([f.rule for f in refused], ["vague-verb"])
        self.assertEqual(refused[0].line, 6)

    def test_a_label_on_another_variable_is_not_this_windows(self):
        after = 'other.set_primary_action(__("Submit"), () => {});\n'
        self.assertEqual(rules(dialog(self.ONE + 'primary_action_label: __("Add"),\n', after=after)), [])

    def test_a_label_far_after_the_window_is_not_read(self):
        after = "\n" * (dialogs.FOLLOW_LINES + 5) + 'd.set_primary_action(__("Submit"), () => {});\n'
        self.assertEqual(rules(dialog(self.ONE + 'primary_action_label: __("Add"),\n', after=after)), [])

    def test_a_label_held_in_a_variable_is_not_judged(self):
        self.assertEqual(rules(dialog(self.ONE + "primary_action_label: label,\n")), [])

    def test_a_window_in_a_comment_is_not_judged(self):
        text = '// const d = new frappe.ui.Dialog({primary_action_label: __("Submit")});\n'
        text += '/* new frappe.ui.Dialog({primary_action_label: "OK", fields: [{fieldtype: "Data"}]}) */\n'
        self.assertEqual(rules(text), [])

    def test_a_window_in_a_string_is_not_judged(self):
        self.assertEqual(rules('const help = "new frappe.ui.Dialog({primary_action_label: \'Submit\'})";\n'), [])

    def test_a_template_literal_and_a_regular_expression_do_not_lose_the_reader(self):
        text = 'const t = `it\'s ${a ? "{" : `}`}`;\nconst r = /[\'"`]/g;\n'
        text += dialog(self.ONE + 'primary_action_label: __("Submit"),\n')
        self.assertEqual(rules(text), ["vague-verb"])


class TheVeil(unittest.TestCase):
    def test_a_rule_fixed_over_the_whole_screen(self):
        refused = found(".my-veil {\n  position: fixed;\n  inset: 0;\n  background: rgba(0, 0, 0, .4);\n}\n", path=CSS)
        self.assertEqual([f.rule for f in refused], ["hand-made-veil"])
        self.assertEqual(refused[0].line, 2)

    def test_the_four_sides_and_the_full_viewport(self):
        for body in (
            "position: fixed; top: 0; left: 0; right: 0; bottom: 0;",
            "position: fixed !important; top: 0px; left: 0; width: 100vw; height: 100vh;",
            "position:fixed;width:100%;height:100%;top:0;left:0",
            "inset: 0 0 0 0; position: fixed;",
        ):
            self.assertEqual(rules(".v { " + body + " }\n", path=CSS), ["hand-made-veil"], body)

    def test_a_drawer_or_a_bar_is_no_veil(self):
        for body in (
            "position: fixed; top: 0; left: 0; bottom: 0; width: 320px;",
            "position: fixed; top: 0; left: 0; right: 0; height: 56px;",
            "position: absolute; inset: 0;",
            "position: fixed; inset: 0 0 0 auto; width: 400px;",
        ):
            self.assertEqual(rules(".v { " + body + " }\n", path=CSS), [], body)

    def test_a_layer_every_click_goes_through_is_no_veil(self):
        text = "Object.assign(box.style, {\n  position: 'fixed', pointerEvents: 'none',\n"
        text += "  top: '0', left: '0', width: '100%', height: '100%',\n});\n"
        self.assertEqual(rules(text), [])
        self.assertEqual(rules(".ink { position: fixed; inset: 0; pointer-events: none }\n", path=CSS), [])

    def test_the_portal_has_no_desk_behind_it(self):
        veil = "<style>\n.sign-veil { position: fixed; inset: 0 }\n</style>\n"
        self.assertEqual(rules(veil, path="app/www/sign/index.html"), [])
        self.assertEqual(rules(veil, path="app/templates/pages/x.html"), [])
        self.assertEqual(rules(veil, path="app/public/js/templates_list.html"), ["hand-made-veil"])
        script = "<script>\n" + dialog('primary_action_label: __("Submit"),\n') + "</script>\n"
        self.assertEqual(rules(script, path="app/www/sign/index.html"), ["vague-verb"])

    def test_frappes_own_backdrop_is_not_ours(self):
        self.assertEqual(rules(".modal-backdrop { position: fixed; inset: 0; opacity: .3 }\n", path=CSS), [])
        self.assertEqual(rules(".modal.show { position: fixed; inset: 0 }\n", path=CSS), [])

    def test_a_nested_scss_rule_is_read_for_its_own_declarations(self):
        scss = ".shop {\n  .veil {\n    position: fixed;\n    &:hover { color: red; }\n    inset: 0;\n  }\n}\n"
        self.assertEqual(rules(scss, path="app/public/scss/x.scss"), ["hand-made-veil"])

    def test_a_style_object_in_a_script(self):
        text = '$(el).css({ position: "fixed", top: 0, left: 0, width: "100%", height: "100%", zIndex: 1050 });\n'
        self.assertEqual(rules(text), ["hand-made-veil"])

    def test_an_inline_style_in_markup(self):
        text = "$(`<div class=\"neo-veil\" style=\"position:fixed;inset:0;background:#0006\"></div>`).appendTo(body);\n"
        self.assertEqual(rules(text), ["hand-made-veil"])

    def test_a_rule_written_in_a_template_literal(self):
        text = "const css = `\n  .veil {\n    position: fixed;\n    inset: 0;\n  }\n`;\n"
        self.assertEqual(rules(text), ["hand-made-veil"])

    def test_a_vue_style_and_a_vue_binding(self):
        vue = (
            '<template>\n  <div :style="{ position: \'fixed\', inset: 0 }"></div>\n</template>\n'
            "<style scoped>\n.cover { position: fixed; top: 0; right: 0; bottom: 0; left: 0; }\n</style>\n"
        )
        self.assertEqual(rules(vue, path="app/frontend/src/X.vue"), ["hand-made-veil", "hand-made-veil"])

    def test_a_bootstrap_modal_built_by_hand(self):
        for text in (
            "$('<div class=\"modal fade\" tabindex=\"-1\"><div class=\"modal-dialog\"></div></div>').appendTo(body);\n",
            "const m = document.createElement('div');\nm.className = 'modal';\n",
            "$wrap.modal('show');\n",
            "d.$wrapper.modal({ backdrop: 'static', keyboard: false });\n",
            "new bootstrap.Modal(el).show();\n",
            "const m = frappe.get_modal(__('Preview'), body);\n",
        ):
            self.assertEqual(rules(text), ["hand-made-veil"], text)

    def test_hiding_a_modal_or_a_class_that_only_starts_with_modal_is_fine(self):
        for text in (
            "$('.modal.show').modal('hide');\n",
            "$('<div class=\"modal-body-text\"></div>');\n",
            "const s = 'call .modal(\"show\") yourself';\n",
            "// $wrap.modal('show');\n",
        ):
            self.assertEqual(rules(text), [], text)


class TheWidth(unittest.TestCase):
    def test_a_width_set_on_the_modal_dialog(self):
        for text in (
            'd.$wrapper.find(".modal-dialog").css("max-width", "900px");\n',
            "d.$wrapper.find('.modal-dialog').css('width', '80vw');\n",
            "d.$wrapper.find(\".modal-dialog\").css({ maxWidth: `min(${w}px, 94vw)` });\n",
            'd.$wrapper.find(".modal-dialog").css({\n  "max-width": "1200px",\n  margin: "auto",\n});\n',
            'd.$wrapper.find(".modal-content").width(1000);\n',
            'document.querySelector(".modal-dialog").style.maxWidth = "900px";\n',
            'd.$wrapper.find(".modal-dialog").addClass("modal-lg");\n',
            "$(d.wrapper).addClass('modal-xl');\n",
        ):
            refused = found(text)
            self.assertEqual([f.rule for f in refused], ["own-width"], text)
            self.assertIn("declare size instead", refused[0].what)

    def test_another_style_on_the_modal_dialog_is_not_a_width(self):
        for text in (
            'd.$wrapper.find(".modal-dialog").css("margin-top", "10px");\n',
            'd.$wrapper.find(".modal-body").css("max-width", "900px");\n',
            'd.$wrapper.addClass("my-dialog");\n',
        ):
            self.assertEqual(rules(text), [], text)

    def test_a_css_rule_that_sizes_a_window(self):
        for css in (
            ".my-dialog .modal-dialog { max-width: 900px; }\n",
            ".x > .modal-content { min-width: 700px }\n",
            ".modal-xl { max-width: 1400px }\n",
            ".a .modal-dialog.modal-lg, .b { width: 60vw }\n",
        ):
            self.assertEqual(rules(css, path=CSS), ["own-width"], css)

    def test_the_message_names_the_part_of_the_selector_that_sizes_the_window(self):
        css = ".a .modal-dialog.pay-dialog,\nbody[data-route='point-of-sale'] .twint-dialog .modal-dialog "
        css += "{ max-width: 900px }\n"
        refused = found(css, path=CSS)
        self.assertEqual([f.rule for f in refused], ["own-width"])
        self.assertTrue(refused[0].what.startswith(".a .modal-dialog.pay-dialog gives a window its max-width"))

    def test_a_rule_on_an_element_inside_the_window_is_not_its_width(self):
        self.assertEqual(rules(".modal-dialog .preview { width: 100%; }\n", path=CSS), [])

    def test_a_class_added_to_the_modal_dialog_and_sized_in_the_same_change(self):
        script = 'd.$wrapper.find(".modal-dialog").addClass("wide-dlg");\n'
        aliases = dialogs.Source(JS, script).aliases()
        self.assertEqual(aliases, {"wide-dlg"})
        self.assertEqual(rules(".wide-dlg { max-width: 1200px; }\n", path=CSS, aliases=aliases), ["own-width"])
        self.assertEqual(rules(".wide-dlg { max-width: 1200px; }\n", path=CSS), [])


class TheExceptions(unittest.TestCase):
    SUBMIT = 'fields: [{fieldtype: "Data", fieldname: "a"}],\nprimary_action_label: __("Submit"),\n'

    def test_a_reason_on_the_window_line(self):
        marked = "({ // dialog-check: ok vague-verb the hub's own form says Submit\n"
        text = dialog(self.SUBMIT).replace("({\n", marked, 1)
        self.assertEqual(rules(text), [])

    def test_a_reason_on_the_line_above(self):
        text = "// dialog-check: ok vague-verb a word the accountant reads on the bank's screen\n" + dialog(self.SUBMIT)
        self.assertEqual(rules(text), [])

    def test_a_reason_on_the_offending_line(self):
        text = dialog(self.SUBMIT.replace('("Submit"),', '("Submit"), // dialog-check: ok vague-verb the bank says it'))
        self.assertEqual(rules(text), [])

    def test_a_reason_names_its_rule(self):
        text = "// dialog-check: ok own-width the window is sized by its preview\n" + dialog(self.SUBMIT)
        self.assertEqual(rules(text), ["vague-verb"])

    def test_several_rules(self):
        text = "// dialog-check: ok size-missing,vague-verb the import mapping of the bank's file\n"
        text += dialog(f'fields: [{FIVE_FIELDS}],\nprimary_action_label: "OK",\n')
        self.assertEqual(rules(text), [])

    def test_no_reason_no_exception_and_the_marker_is_refused(self):
        text = "// dialog-check: ok vague-verb\n" + dialog(self.SUBMIT)
        self.assertEqual(sorted(rules(text)), ["marker", "vague-verb"])

    def test_a_marker_that_names_no_rule_is_refused(self):
        text = "// dialog-check: ok because the screen is full of things\n" + dialog(self.SUBMIT)
        self.assertEqual(sorted(rules(text)), ["marker", "vague-verb"])

    def test_a_reason_in_css(self):
        css = "/* dialog-check: ok hand-made-veil the till takes the whole screen */\n.till {\n  position: fixed;\n"
        css += "  inset: 0;\n}\n"
        self.assertEqual(rules(css, path=CSS), [])
        inside = ".till {\n  position: fixed; /* dialog-check: ok hand-made-veil a kiosk has no desk behind it */\n"
        self.assertEqual(rules(inside + "  inset: 0;\n}\n", path=CSS), [])


class WhatIsNew(unittest.TestCase):
    OLD = dialog(f'fields: [{FIVE_FIELDS}],\nprimary_action_label: __("Submit"),\n') + "\n\nfunction other() {\n}\n"

    def test_the_stock_untouched_by_the_change_passes(self):
        new = self.OLD.replace("function other() {\n}", "function other() {\n  return 1;\n}")
        self.assertEqual(rules(new, base=self.OLD), [])

    def test_a_window_whose_lines_changed_is_judged_whole(self):
        new = self.OLD.replace('fieldtype: "Date"}', 'fieldtype: "Date", reqd: 1}')
        self.assertEqual(sorted(rules(new, base=self.OLD)), ["size-missing", "vague-verb"])

    def test_a_line_removed_from_a_window_judges_it(self):
        old = dialog(f'size: "large",\nfields: [{FIVE_FIELDS}],\n')
        new = old.replace('size: "large",\n', "")
        self.assertEqual(rules(new, base=old), ["size-missing"])

    def test_a_new_indentation_is_no_change(self):
        new = "\n".join("    " + line if line else line for line in self.OLD.splitlines()) + "\n"
        self.assertEqual(rules(new, base=self.OLD), [])

    def test_a_new_window_beside_the_stock_is_judged(self):
        new = self.OLD + dialog('fields: [{fieldtype: "Data", fieldname: "x"}],\nprimary_action_label: "Valider",\n')
        refused = found(new, base=self.OLD)
        self.assertEqual([f.rule for f in refused], ["vague-verb"])
        self.assertGreater(refused[0].line, 15)

    def test_a_veil_of_the_stock_is_left_alone_and_a_new_line_of_it_is_judged(self):
        old = ".veil {\n  position: fixed;\n  inset: 0;\n}\n"
        self.assertEqual(rules(old.replace("inset: 0;\n", "inset: 0;\n  color: red;\n"), base=old, path=CSS), [])
        self.assertEqual(rules(".veil {\n  position: fixed;\n  inset: 0;\n}\n", base=".veil {\n}\n", path=CSS),
                         ["hand-made-veil"])

    def test_a_marker_of_the_stock_is_left_alone(self):
        old = "// dialog-check: ok\nconst x = 1;\n"
        self.assertEqual(rules(old + "const y = 2;\n", base=old), [])

    def test_what_is_not_ours_or_not_written_by_hand_is_not_read(self):
        text = dialog('primary_action_label: __("Submit"),\n')
        for path in (
            "node_modules/x/index.js",
            "app/public/dist/x.js",
            "app/public/js/x.min.js",
            "app/public/craft/editor.js",
            "app/public/build/x.js",
            "cypress/integration/x.js",
            "app/public/js/lib/grapes.js",
            "app/public/css/dialog_model.css",
            "app/public/embed/assets/index-DLplWssb.js",
            "docs/news/page.html",
            "design/phone/screen.dc.html",
            "app/x.py",
        ):
            self.assertEqual(rules(text, path=path), [], path)
        self.assertEqual(rules(("x" * 900 + ";") * 10 + "\n" + text), [])  # a bundle

    def test_a_test_file_is_judged(self):
        self.assertEqual(rules(dialog('primary_action_label: __("Submit"),\n'), path="app/tests/test_x.js"),
                         ["vague-verb"])

    def test_broken_code_is_the_build_s_business(self):
        for text in ("new frappe.ui.Dialog({ fields: [ {", "`unclosed ${ new frappe.ui.Dialog({", "/* never closed"):
            dialogs.new_findings(JS, text, None)


class TheRepository(unittest.TestCase):
    STOCK = dialog(f'fields: [{FIVE_FIELDS}],\nprimary_action_label: __("Submit"),\n')

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = self.folder.name
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.com")
        self.git("config", "user.name", "ci")
        self.write("shop/public/js/old.js", self.STOCK)
        self.write("shop/public/css/old.css", ".veil { position: fixed; inset: 0 }\n")
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
        script = os.path.abspath(dialogs.__file__)
        return subprocess.run(
            [sys.executable, script, "--root", self.root, *arguments], capture_output=True, text=True, env=env
        )

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def test_nothing_changed_nothing_refused(self):
        self.assertEqual(self.script("--base", "HEAD").returncode, 0)

    def test_a_new_file_is_refused_against_the_base(self):
        self.write("shop/public/js/new.js", dialog('fields: [{fieldtype: "Data"}],\nprimary_action_label: "OK",\n'))
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("shop/public/js/new.js:3: vague-verb", done.stdout)
        self.assertNotIn("old.js", done.stdout)

    def test_the_stock_stays_green(self):
        self.write("shop/public/js/old.js", self.STOCK + "\nconsole.log(1);\n")
        self.write("README.md", "x")
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_renamed_file_is_judged_against_what_it_was(self):
        self.git("mv", "shop/public/js/old.js", "shop/public/js/moved.js")
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_class_and_its_rule_in_two_files_of_the_change(self):
        self.write("shop/public/js/wide.js", 'd.$wrapper.find(".modal-dialog").addClass("wide-dlg");\n')
        self.write("shop/public/css/wide.css", ".wide-dlg { max-width: 1200px }\n")
        self.commit()
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("wide.css:1: own-width", done.stdout)

    def test_a_merge_of_upstream_is_not_judged(self):
        self.git("checkout", "-q", "-b", "upstream")
        self.write("shop/public/js/theirs.js", dialog('primary_action_label: __("Submit"),\n'))
        self.commit("theirs")
        self.git("checkout", "-q", "-")
        self.write("README.md", "ours")
        self.commit("ours")
        self.git("merge", "-q", "--no-edit", "upstream")
        done = self.script("--base", "HEAD~1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("merge", done.stdout)

    def test_staged(self):
        self.write("shop/public/css/new.css", ".v { position: fixed; top: 0; left: 0; right: 0; bottom: 0 }\n")
        self.git("add", "shop/public/css/new.css")
        done = self.script("--staged")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("hand-made-veil", done.stdout)

    def test_a_file_on_disk_against_head(self):
        self.write("shop/public/js/old.js", self.STOCK.replace('"Date"}', '"Date", reqd: 1}'))
        path = os.path.join(self.root, "shop/public/js/old.js")
        done = self.script(path)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("size-missing", done.stdout)
        self.assertEqual(self.script(os.path.join(self.root, "shop/public/css/old.css")).returncode, 0)

    def test_the_annotations_of_github(self):
        self.write("shop/public/js/new.js", dialog('primary_action_label: __("Submit"),\n'))
        self.commit()
        env = dict(os.environ, GITHUB_ACTIONS="true")
        done = subprocess.run(
            [sys.executable, os.path.abspath(dialogs.__file__), "--root", self.root, "--base", "HEAD~1"],
            capture_output=True, text=True, env=env,
        )
        self.assertIn("::error file=shop/public/js/new.js,line=2::vague-verb", done.stdout)

    def test_the_inventory_lists_the_stock_and_refuses_nothing(self):
        done = self.script("--inventory", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = json.loads(done.stdout)
        window = next(r for r in rows if r["kind"] == "Dialog")
        self.assertEqual(window["file"], "shop/public/js/old.js")
        self.assertEqual(window["rules"], ["size-missing", "vague-verb"])
        self.assertEqual(window["fields"], 5)
        self.assertIn("Submit", window["detail"])
        veil = next(r for r in rows if r["kind"] == "veil")
        where = (veil["file"], veil["line"], veil["rules"])
        self.assertEqual(where, ("shop/public/css/old.css", 1, ["hand-made-veil"]))
        text = self.script("--inventory")
        self.assertEqual(text.returncode, 0)
        self.assertIn("vague-verb", text.stdout)

    def test_the_inventory_keeps_a_width_with_its_window(self):
        self.write("shop/public/js/w.js", dialog("size: 'large',\nfields: [],\n", after=(
            'd.$wrapper.find(".modal-dialog").css("max-width", "900px");\n')))
        rows = json.loads(self.script("--inventory", "--json").stdout)
        window = next(r for r in rows if r["file"] == "shop/public/js/w.js")
        self.assertEqual((window["kind"], window["rules"]), ("Dialog", ["own-width"]))
        self.assertEqual([r for r in rows if r["file"] == "shop/public/js/w.js" and r["kind"] != "Dialog"], [])

    def test_a_base_is_required(self):
        self.assertEqual(self.script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
