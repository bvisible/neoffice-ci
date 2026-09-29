"""The translator must never write a translation identical to its source.

Frappe merges every installed app's catalogue into ONE flat dict, in
installation order, last app winning. So a catalogue answering "System Manager"
for "System Manager" does not say nothing: it ERASES the real translation
another app ships, on every screen of the site.

Measured on the dev instance on 2026-09-10: 1345 msgids where two installed apps
disagree, 43 of them decided by an identity -- "System Manager" (disputed by 29
apps), "ID", "Stock Entry", "Stock Manager", "POS"… all read in English by the
whole fleet because one catalogue said the English word back
(neoffice-maintenance#335).

An empty msgstr says the same thing at no cost: the screen falls back to the
source text either way, and another app's translation is free to apply.
"""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import polib

sys.path.insert(0, str(Path(__file__).resolve().parent))

import translate_po  # noqa: E402


def _answer(pairs):
    """Fake the model: return this exact JSON array."""
    import json

    return json.dumps([{"i": i, "t": t} for i, t in pairs])


class TestIdentityIsRefused(unittest.TestCase):
    def _translate(self, items, answers):
        with patch.object(translate_po, "_claude", return_value=_answer(answers)):
            return translate_po.translate_batch(items, "fr", "sonnet")

    def test_an_identity_is_dropped(self):
        got = self._translate(["System Manager"], [(0, "System Manager")])
        self.assertEqual(got, {}, "an identity must never be written")

    def test_an_identity_differing_only_by_surrounding_space_is_dropped(self):
        got = self._translate(["Notes"], [(0, "  Notes  ")])
        self.assertEqual(got, {})

    def test_a_real_translation_still_passes(self):
        got = self._translate(["Stock Entry"], [(0, "Écriture de stock")])
        self.assertEqual(got, {0: "Écriture de stock"})

    def test_a_case_change_is_a_real_translation(self):
        """`email` -> `E-mail` differs, and differing is the whole point."""
        got = self._translate(["email"], [(0, "E-mail")])
        self.assertEqual(got, {0: "E-mail"})

    def test_the_batch_keeps_the_good_ones_around_a_refused_identity(self):
        got = self._translate(
            ["Stock Entry", "System Manager", "Table"],
            [(0, "Écriture de stock"), (1, "System Manager"), (2, "Tableau")],
        )
        self.assertEqual(got, {0: "Écriture de stock", 2: "Tableau"})

    def test_a_placeholder_mismatch_is_still_refused(self):
        """The guard that already existed must survive the new one."""
        got = self._translate(["Hello {0}"], [(0, "Bonjour")])
        self.assertEqual(got, {})


LIMIT_REPLY = {"is_error": True, "result": "You've hit your weekly limit · resets Sep 28, 8pm (UTC)"}


def _cli(returncode=0, stdout="", stderr=""):
    """What `subprocess.run` hands back for one call of the Claude CLI."""
    return subprocess.CompletedProcess(["claude"], returncode, stdout, stderr)


def _limit_cli():
    """The limit as the CLI reports it: on STDOUT, as JSON, with a non-zero exit and an empty stderr."""
    return _cli(returncode=1, stdout=json.dumps(LIMIT_REPLY))


def _ok_cli(pairs):
    """A successful call whose model answer is the JSON array of `pairs`."""
    return _cli(stdout=json.dumps({"is_error": False, "result": _answer(pairs)}))


class TestTheCallReportsAnAccountLimit(unittest.TestCase):
    """`_claude` tells a usage limit from any other failure (#827).

    The CLI writes its error on STDOUT and leaves stderr empty. The translator read stderr only, so a
    limit was logged as « claude exited 1: » with nothing after it and treated like a transient failure.
    """

    def _call(self, completed):
        with patch.object(translate_po.subprocess, "run", return_value=completed):
            return translate_po._claude("prompt", "sonnet")

    def test_a_limit_on_stdout_with_a_non_zero_exit_is_recognised(self):
        with self.assertRaises(translate_po.AccountLimitError) as caught:
            self._call(_limit_cli())
        self.assertIn("weekly limit", str(caught.exception))
        self.assertIn("resets Sep 28", str(caught.exception))

    def test_a_limit_reported_with_a_zero_exit_is_recognised(self):
        with self.assertRaises(translate_po.AccountLimitError):
            self._call(_cli(returncode=0, stdout=json.dumps(LIMIT_REPLY)))

    def test_a_plain_text_limit_message_is_recognised(self):
        with self.assertRaises(translate_po.AccountLimitError):
            self._call(_cli(returncode=1, stdout="Claude usage limit reached. Your limit will reset at 8pm (UTC)"))

    def test_another_failure_is_not_taken_for_a_limit(self):
        with self.assertRaises(translate_po.ClaudeCallError) as caught:
            self._call(_cli(returncode=1, stderr="Invalid API key"))
        self.assertNotIsInstance(caught.exception, translate_po.AccountLimitError)
        self.assertIn("Invalid API key", str(caught.exception))

    def test_a_dropped_connection_is_not_taken_for_a_limit(self):
        with self.assertRaises(translate_po.ClaudeCallError) as caught:
            self._call(_cli(returncode=1, stdout=json.dumps({"is_error": True, "result": "Connection reset by peer"})))
        self.assertNotIsInstance(caught.exception, translate_po.AccountLimitError)

    def test_a_good_answer_that_says_resets_is_not_a_limit(self):
        """A string of the app may say « resets »: only a FAILED call is ever scanned."""
        answer = _answer([(0, "Réinitialise le filtre (resets the filter)")])
        got = self._call(_cli(stdout=json.dumps({"is_error": False, "result": answer})))
        self.assertEqual(got, answer)

    def test_a_missing_cli_or_a_timeout_is_a_failed_call(self):
        for error in (FileNotFoundError("claude"), subprocess.TimeoutExpired("claude", 300)):
            with patch.object(translate_po.subprocess, "run", side_effect=error):
                with self.assertRaises(translate_po.ClaudeCallError):
                    translate_po._claude("prompt", "sonnet")

    def test_a_verbose_reply_is_read_by_its_last_result(self):
        messages = [{"type": "system"}, {"type": "result", "is_error": False, "result": "[]"}]
        self.assertEqual(self._call(_cli(stdout=json.dumps(messages))), "[]")


class TestTheNightFailsWhenNothingCanBeTranslated(unittest.TestCase):
    """`main()`'s exit code: a limit, or a run where every call failed, must not stay green (#827).

    Measured on 2026-09-27 and 28: the nightly translation of a fleet app ended green with 0 of 189
    strings filled, the account being at its weekly limit; the run of the 29th, after the reset, filled
    172 of 189. Nothing told anybody in between.
    """

    BATCHES = 3  # msgids in the file, translated one per batch

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "fr.po"
        po = polib.POFile()
        po.metadata = {"Language": "fr", "Content-Type": "text/plain; charset=UTF-8"}
        for i in range(self.BATCHES):
            po.append(polib.POEntry(msgid=f"Item {i}", msgstr=""))
        po.save(str(self.path))

    def _run(self, side_effect):
        """(exit code, everything printed, subprocess.run mock) of one `main()` on the file above."""
        argv = ["translate_po.py", str(self.path), "--locale", "fr", "--batch", "1"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(
            translate_po.subprocess, "run", side_effect=side_effect
        ) as run, contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = translate_po.main()
        return code, out.getvalue(), run

    def _filled(self):
        return {e.msgid: e.msgstr for e in polib.pofile(str(self.path)) if e.msgstr}

    def test_a_reached_limit_fails_the_run_once_with_its_reason(self):
        code, output, run = self._run(lambda *a, **k: _limit_cli())
        self.assertEqual(code, 1)
        self.assertIn("limit", output.lower())
        self.assertIn("resets Sep 28", output)
        self.assertEqual(run.call_count, 1, "the run must stop at the first batch, not ask twice")
        self.assertEqual(self._filled(), {})

    def test_a_limit_met_after_some_batches_keeps_what_was_filled(self):
        replies = [_ok_cli([(0, "Élément 0")]), _limit_cli()]
        code, output, run = self._run(lambda *a, **k: replies.pop(0))
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self._filled(), {"Item 0": "Élément 0"})

    def test_a_run_where_every_call_fails_fails(self):
        code, output, run = self._run(lambda *a, **k: _cli(returncode=1, stderr="Invalid API key"))
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, self.BATCHES, "another failure must not stop the other batches")
        self.assertIn("Invalid API key", output)
        self.assertEqual(self._filled(), {})

    def test_a_good_run_is_a_success_and_writes_its_translations(self):
        replies = [_ok_cli([(0, f"Élément {i}")]) for i in range(self.BATCHES)]
        code, output, run = self._run(lambda *a, **k: replies.pop(0))
        self.assertEqual(code, 0)
        self.assertEqual(self._filled(), {f"Item {i}": f"Élément {i}" for i in range(self.BATCHES)})

    def test_a_partial_failure_is_still_a_success(self):
        replies = [_cli(returncode=1, stderr="boom"), _ok_cli([(0, "Élément 1")]), _ok_cli([(0, "Élément 2")])]
        code, output, run = self._run(lambda *a, **k: replies.pop(0))
        self.assertEqual(code, 0)
        self.assertEqual(self._filled(), {"Item 1": "Élément 1", "Item 2": "Élément 2"})

    def test_an_answer_with_nothing_safe_to_write_is_still_a_success(self):
        """The model answered: every string was an identity and got dropped. That is not a failed run."""
        replies = [_ok_cli([(0, f"Item {i}")]) for i in range(self.BATCHES)]
        code, output, run = self._run(lambda *a, **k: replies.pop(0))
        self.assertEqual(code, 0)
        self.assertEqual(self._filled(), {})

    def test_nothing_to_translate_is_a_success_without_calling_the_model(self):
        po = polib.POFile()
        po.metadata = {"Language": "fr"}
        po.append(polib.POEntry(msgid="Done", msgstr="Fait"))
        po.save(str(self.path))
        code, output, run = self._run(lambda *a, **k: self.fail("the model must not be called"))
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 0)


class TestTheRealCommandLine(unittest.TestCase):
    """The same, through a real subprocess: a stand-in `claude` on the PATH prints what the CLI prints.

    The stand-in records every call, so a run that stops at the first batch (a limit) is told apart from
    one that went on asking (any other failure): the text of the message alone would not tell them apart.
    """

    MSGIDS = 3  # one batch each

    def _fake_cli(self, directory, reply, exit_code=0):
        """A `claude` that logs its arguments, prints `reply` on stdout and exits with `exit_code`."""
        calls = Path(directory) / "calls.txt"
        script = Path(directory) / "claude"
        script.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" >> "{calls}"\n'
            f"cat <<'EOF'\n{reply}\nEOF\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return calls

    def _main(self, directory):
        po = Path(directory) / "fr.po"
        file = polib.POFile()
        file.metadata = {"Language": "fr"}
        for i in range(self.MSGIDS):
            file.append(polib.POEntry(msgid=f"Save {i}", msgstr=""))
        file.save(str(po))
        out = io.StringIO()
        env = dict(os.environ, PATH=f"{directory}{os.pathsep}{os.environ['PATH']}")
        argv = ["translate_po.py", str(po), "--locale", "fr", "--batch", "1"]
        with patch.dict(os.environ, env), patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = translate_po.main()
        return code, out.getvalue(), po

    def test_the_limit_printed_by_the_cli_fails_the_run_at_the_first_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = self._fake_cli(directory, json.dumps(dict(LIMIT_REPLY, type="result")), exit_code=1)
            code, output, po = self._main(directory)
            self.assertEqual(code, 1, output)
            self.assertIn("account limit reached", output)
            self.assertIn("resets Sep 28", output)
            arguments = calls.read_text(encoding="utf-8").split()
            self.assertEqual(arguments.count("-p"), 1, "the run asked again after the limit")
            self.assertEqual(arguments[arguments.index("--output-format") + 1], "json")
            self.assertEqual([e.msgstr for e in polib.pofile(str(po))], [""] * self.MSGIDS)

    def test_any_other_failure_of_the_cli_goes_on_asking_and_then_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = self._fake_cli(directory, "something went wrong", exit_code=1)
            code, output, _ = self._main(directory)
            self.assertEqual(code, 1, output)
            self.assertNotIn("account limit reached", output)
            self.assertEqual(calls.read_text(encoding="utf-8").split().count("-p"), self.MSGIDS)

    def test_a_good_reply_printed_by_the_cli_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            answer = json.dumps({"type": "result", "is_error": False, "result": _answer([(0, "Enregistrer")])})
            self._fake_cli(directory, answer)
            code, output, po = self._main(directory)
            self.assertEqual(code, 0, output)
            self.assertEqual([e.msgstr for e in polib.pofile(str(po))], ["Enregistrer"] * self.MSGIDS)


def _po(path, entries):
    """Write a PO file. Each entry is (msgid, msgstr) or (msgid, msgstr, msgctxt)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    catalogue = polib.POFile()
    catalogue.metadata = {"Language": "fr", "Content-Type": "text/plain; charset=UTF-8"}
    for entry in entries:
        msgid, msgstr, *rest = entry
        catalogue.append(polib.POEntry(msgid=msgid, msgstr=msgstr, msgctxt=rest[0] if rest else None))
    catalogue.save(str(path))
    return path


class TestTheCoreSpeaksFirst(unittest.TestCase):
    """A bare msgid the bench already translates is left EMPTY, not translated again (#619).

    Frappe merges every app's catalogue into one dictionary, last app winning, so a bare msgid this app
    translates replaces the core's word on every screen: « Solde » read « Équilibre » and « Commande »
    « Ordre » across the whole desk of an instance, because a fitness app said so. The night used to
    translate every empty msgstr, the core's words included, so deleting such an entry from an app's
    catalogue lasted until the next night. An empty value is ignored by Frappe: the desk keeps the
    core's word, and the core can correct it later without a stale copy overriding the correction.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bench = Path(self._tmp.name) / "frappe-bench"
        self.apps = self.bench / "apps"
        _po(
            self.apps / "frappe" / "frappe" / "locale" / "fr.po",
            [
                ("Save", "Enregistrer"),
                ("Back", "Retour"),
                ("Group Class", "Group Class"),  # an identity: the English word said back
            ],
        )
        self.target = _po(
            self.apps / "myapp" / "myapp" / "locale" / "fr.po",
            [("Save", ""), ("Brand new", ""), ("Group Class", "")],
        )

    def _core_entry(self, app, entries):
        return _po(self.apps / app / app / "locale" / "fr.po", entries)

    def _run(self, *extra, path=None):
        """(exit code, output, msgids the model was asked for) of one `main()` on the target file."""
        asked = []

        def model(command, **kwargs):
            prompt = command[command.index("-p") + 1]
            items = json.loads(prompt.split("Translate these items:\n", 1)[1])
            asked.extend(item["s"] for item in items)
            answer = json.dumps([{"i": item["i"], "t": "FR " + item["s"]} for item in items], ensure_ascii=False)
            return _cli(stdout=json.dumps({"is_error": False, "result": answer}))

        argv = ["translate_po.py", str(path or self.target), "--locale", "fr", *extra]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(translate_po.subprocess, "run", side_effect=model):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = translate_po.main()
        return code, out.getvalue(), asked

    def _entries(self, path=None):
        return {(e.msgid, e.msgctxt): e.msgstr for e in polib.pofile(str(path or self.target))}

    def test_a_msgid_frappe_translates_is_not_asked_and_stays_empty(self):
        code, output, asked = self._run()
        self.assertEqual(code, 0, output)
        self.assertEqual(asked.count("Save"), 0, "the core's word must not be translated again")
        self.assertEqual(self._entries()[("Save", None)], "")
        self.assertIn("left empty", output)
        self.assertIn("'Save' (frappe)", output)

    def test_a_msgid_the_core_does_not_know_is_translated_as_before(self):
        code, output, asked = self._run()
        self.assertEqual(self._entries()[("Brand new", None)], "FR Brand new")
        self.assertIn("Brand new", asked)

    def test_the_csv_of_a_dependency_app_counts_too(self):
        csv_path = self.apps / "erpnext" / "erpnext" / "translations" / "fr.csv"
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("Customer,Client\nOrder,Commande\n", encoding="utf-8")
        _po(self.target, [("Customer", ""), ("Brand new", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Brand new"])
        self.assertIn("erpnext", output)

    def test_a_csv_row_with_a_context_is_no_bare_word(self):
        csv_path = self.apps / "erpnext" / "erpnext" / "translations" / "fr.csv"
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("Plan,Forfait,Subscription\n", encoding="utf-8")
        _po(self.target, [("Plan", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Plan"])

    def test_the_po_of_an_app_wins_over_its_csv_as_frappe_merges_them(self):
        """The CSV says a word, the PO says the identity: the merged word is the identity, which says nothing."""
        csv_path = self.apps / "erpnext" / "erpnext" / "translations" / "fr.csv"
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("Item,Article\n", encoding="utf-8")
        self._core_entry("erpnext", [("Item", "Item")])
        _po(self.target, [("Item", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Item"])

    def test_an_identity_in_the_core_says_nothing_so_the_model_is_asked(self):
        """`Group Class` said back in English is no word the desk has: a real translation is still wanted."""
        code, output, asked = self._run()
        self.assertIn("Group Class", asked)
        self.assertEqual(self._entries()[("Group Class", None)], "FR Group Class")

    def test_an_entry_with_a_context_is_another_key_and_is_translated_as_before(self):
        _po(self.target, [("Save", "", "Gym"), ("Save", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Save"], "only the entry with a context is asked")
        entries = self._entries()
        self.assertEqual(entries[("Save", "Gym")], "FR Save")
        self.assertEqual(entries[("Save", None)], "")

    def test_a_context_entry_of_the_core_is_no_bare_word(self):
        self._core_entry("erpnext", [("Stock", "Stock physique", "Warehouse")])
        _po(self.target, [("Stock", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Stock"])

    def test_an_obsolete_or_plural_entry_of_the_core_says_nothing(self):
        locale = self.apps / "erpnext" / "erpnext" / "locale"
        locale.mkdir(parents=True)
        catalogue = polib.POFile()
        catalogue.metadata = {"Language": "fr"}
        catalogue.append(polib.POEntry(msgid="Old", msgstr="Ancien", obsolete=True))
        catalogue.append(polib.POEntry(msgid="File", msgid_plural="Files", msgstr_plural={0: "Fichier", 1: "Fichiers"}))
        catalogue.save(str(locale / "fr.po"))
        _po(self.target, [("Old", ""), ("File", "")])
        code, output, asked = self._run()
        self.assertEqual(sorted(asked), ["File", "Old"])

    def test_the_apps_own_catalogue_is_not_the_vocabulary(self):
        csv_path = self.apps / "myapp" / "myapp" / "translations" / "fr.csv"
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("Word,Mot\n", encoding="utf-8")
        _po(self.target, [("Word", "")])
        code, output, asked = self._run()
        self.assertEqual(asked, ["Word"])

    def test_when_the_core_knows_every_empty_msgid_the_model_is_not_called_and_the_run_succeeds(self):
        _po(self.target, [("Save", ""), ("Back", "")])
        code, output, asked = self._run()
        self.assertEqual(code, 0, output)
        self.assertEqual(asked, [])
        self.assertIn("nothing to do", output)
        self.assertEqual(self._entries(), {("Save", None): "", ("Back", None): ""})

    def test_what_is_left_to_the_core_does_not_count_against_the_cap(self):
        _po(self.target, [("Save", ""), ("Back", ""), ("New A", ""), ("New B", "")])
        code, output, asked = self._run("--max", "1")
        self.assertEqual(asked, ["New A"])
        self.assertEqual(self._entries()[("New B", None)], "")

    def test_a_translation_already_there_is_never_touched(self):
        _po(self.target, [("Save", "Sauvegarder"), ("Brand new", "")])
        code, output, asked = self._run()
        self.assertEqual(self._entries()[("Save", None)], "Sauvegarder")

    def test_outside_a_bench_everything_is_translated_as_before(self):
        loose = _po(Path(self._tmp.name) / "elsewhere" / "fr.po", [("Save", ""), ("Brand new", "")])
        code, output, asked = self._run(path=loose)
        self.assertEqual(sorted(asked), ["Brand new", "Save"])
        self.assertNotIn("left empty", output)

    def test_a_broken_dependency_file_does_not_stop_the_night(self):
        broken = self.apps / "erpnext" / "erpnext" / "locale" / "fr.po"
        broken.parent.mkdir(parents=True)
        broken.write_text("this is not a PO file\nmsgid\n", encoding="utf-8")
        code, output, asked = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("Brand new", asked)

    def test_the_vocabulary_names_the_app_that_speaks_and_reads_frappe_first(self):
        self._core_entry("erpnext", [("Customer", "Client"), ("Save", "Sauvegarder")])
        vocabulary, read = translate_po.core_vocabulary(str(self.target), "fr")
        self.assertEqual(read, ["frappe", "erpnext"])
        self.assertEqual(vocabulary["Save"], ("frappe", "Enregistrer"), "the first app to speak is the one named")
        self.assertEqual(vocabulary["Customer"], ("erpnext", "Client"))
        self.assertNotIn("Group Class", vocabulary)
        self.assertNotIn("Brand new", vocabulary)

    def test_the_app_being_translated_is_not_read_as_its_own_vocabulary(self):
        vocabulary, read = translate_po.core_vocabulary(str(self.target), "fr")
        self.assertNotIn("myapp", read)


if __name__ == "__main__":
    unittest.main(verbosity=2)
