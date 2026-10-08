#!/usr/bin/env python3
"""Headless PO translator — fills empty msgstr with Haiku, in CI.

The manual half of the pipeline already exists (translate.sh does
generate-pot-file / update-po-files / compile-po-to-mo; the /translate skill does
the AI step interactively). This is the missing piece: a NON-interactive
translator the CI runs on every push, so new user-facing strings get translated
without anyone opening a session.

Design, on purpose:
- Only ever fills entries whose msgstr is empty. A human translation is never
  overwritten, and a re-run is a no-op — safe to run on every push.
- Never writes a translation identical to the source. Frappe merges every app's
  catalogue into one flat dict, last app installed winning, so an identity does
  not say nothing — it erases what another app translated, site-wide (#335).
- Never asks for a word the desk already has. The same flat dictionary means a bare
  msgid this app translates REPLACES the core's word on every screen (« Solde » read
  « Équilibre », « Commande » « Ordre »: neoffice-maintenance#619). So a bare msgid that
  frappe or a dependency app of the bench (erpnext, payments, neoffice_theme…) already
  translates is left EMPTY: Frappe ignores an empty value, the desk keeps the core's word,
  and the core can correct it later without a stale copy here overriding the correction.
  Copying the core's word instead would freeze it. A msgid with a context is another key
  (`msgid:context`) and is translated as before.
- Never fills a bare msgid this catalogue also carries WITH a context. An app that says its sense of a
  word through a context (`word:context` is looked up before the bare word, so no other app can take
  the sense) has chosen not to own the bare word: a bare entry of its own can only replace the sense
  another app gives it, on every screen. The extractor re-emits such a bare msgid every night (a DocType
  label is read without context), so the night used to undo, each time, what the app had removed on
  purpose: it stays EMPTY, like the core's words, and a human's translation of it is never touched.
- Never fills a bare msgid the app declares removed on purpose: `keep_empty.txt` next to the PO files,
  one msgid per line (blank lines and `#` comments ignored). It covers what the rule above cannot see:
  a word the app dropped altogether, with no contexted twin, because another app's word must win. The
  extractor brings such a msgid back every night (a Select option is read without context) and, with
  the account back, the night put it back each time, a commit that `[skip ci]` kept from any test.
- Placeholders, format specifiers and HTML are preserved verbatim (the model is
  told, and we verify every returned string still carries them; a mismatch is
  dropped, never written).
- The model is Haiku, billed to the subscription via the `claude` CLI print
  mode (CLAUDE_CODE_OAUTH_TOKEN): translation is volume, not reasoning, and
  Haiku 5.5 does it well for much less (fleet rule since 2026-10-08, Sonnet before).
- Fixes the PO `Language:` header while here (an empty one kills `bench build`).

Exit 0 whether or not anything changed, and after a partial failure; prints a
one-line summary. A single failed batch never raises into the workflow: it logs and
moves on, leaving those msgids empty for the next run.

Exit 1 in the two cases where going green would hide that nothing can be translated
(#827): the Claude account behind CI has reached its usage limit (every later call
fails the same way, so the run stops at the first batch), or every call of the run
failed. The night then fails ONCE, with the reason on the run's summary, instead of
staying green for as long as the limit lasts.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import polib

# Tokens that must survive translation untouched. If a returned string loses any
# token the source had, we reject that translation (keep the msgid empty).
_PLACEHOLDER = re.compile(
    r"""(\{[^{}]*\}      # {0}, {name}, {} , {{ jinja }} handled by the {{ below
        |\{\{.*?\}\}      # {{ jinja }}
        |%\([^)]*\)[sdrfg]  # %(name)s
        |%[sdrfgi]        # %s %d …
        |</?[a-zA-Z][^>]*>) # HTML tags
    """,
    re.VERBOSE,
)

RULES = (
    "You translate Frappe ERP UI strings from English to {lang_name} for a Swiss "
    "business audience. Rules, strictly:\n"
    "- Swiss French register when the target is French: vouvoiement, currency CHF, "
    "calm professional tone, no marketing hype.\n"
    "- Preserve EVERY placeholder and tag verbatim and in place: {{0}}, {{name}}, "
    "{{{{ jinja }}}}, %s, %d, %(x)s, and HTML like <b>…</b>. Never translate, "
    "reorder inside, or drop them.\n"
    "- Preserve leading/trailing whitespace and trailing punctuation/colons.\n"
    "- Keep product names, code identifiers, DocType names and units as-is.\n"
    "- Translate the meaning, not word-for-word; keep it short (it is a UI label).\n"
    "- If a string should not change (already a proper noun, a code, a symbol), "
    "return it unchanged.\n"
    "Return ONLY a JSON array of objects {{\"i\": <index>, \"t\": <translation>}}, "
    "one per input item, no prose, no code fence."
)

LANG_NAMES = {"fr": "French", "de": "German", "it": "Italian", "en": "English"}

# What the CLI says when the account behind CI has used its allowance: « You've hit your weekly limit
# · resets Sep 28, 8pm (UTC) », « Claude usage limit reached. Your limit will reset at 8pm », « 5-hour
# limit reached ∙ resets 3pm ». Only ever read on a call that FAILED: a good answer is never scanned,
# so a string of the app that happens to say « resets » cannot be taken for a limit.
_LIMIT_MESSAGE = re.compile(
    r"hit your .*limit|weekly limit|usage limit|session limit|rate limit|limit reached|limit will reset"
    r"|\bresets\b",
    re.IGNORECASE,
)


class AccountLimitError(RuntimeError):
    """The Claude account behind CI has reached its usage limit: no later call can work until it resets."""


class ClaudeCallError(RuntimeError):
    """One call to the Claude CLI failed for a reason that may pass (timeout, transient error, no CLI)."""


def _tokens(s: str) -> list[str]:
    return sorted(_PLACEHOLDER.findall(s))


def _read_reply(stdout: str) -> tuple[bool, str]:
    """(is_error, text) of what `claude -p --output-format json` printed on STDOUT."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, stdout  # some versions print the text directly
    if isinstance(data, list):  # --verbose prints every message: the last "result" is the answer
        data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), {})
    if not isinstance(data, dict):
        return False, stdout
    return bool(data.get("is_error")), str(data.get("result") or "")


def _claude(prompt: str, model: str) -> str:
    """Call the Claude CLI in print mode and return the model's text.

    Raises AccountLimitError when the account's usage limit is reached (every later call would fail
    the same way, so the caller stops), and ClaudeCallError for any other failure of the call.

    The CLI writes its error message on STDOUT, as JSON with `is_error`, and leaves stderr empty. This
    function used to read stderr only: a limit came out as « claude exited 1: » with nothing after it,
    the batch was skipped like any other, and the nightly translation stayed green for two nights with
    0 of 189 strings filled (#827).
    """
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:  # noqa: BLE001
        raise ClaudeCallError(f"claude CLI call failed: {e}") from e
    is_error, text = _read_reply(proc.stdout)
    if proc.returncode == 0 and not is_error:
        return text
    detail = (text or proc.stderr or "").strip()
    if _LIMIT_MESSAGE.search(detail):
        raise AccountLimitError(detail[:300])
    raise ClaudeCallError(f"claude exited {proc.returncode}: {detail[:200] or 'no message'}")


# --- the words the desk already has --------------------------------------------------------------

def _read_po_words(path: Path) -> dict[str, str]:
    """{bare msgid: msgstr} of one PO file. Entries with a context, plurals, obsolete and empty ones say
    nothing a bare msgid could collide with, and are left out. An unreadable file reads as empty: the
    vocabulary is a help, never a reason to stop a night. A file that is not there is normal (frappe has
    no CSV, most apps have no PO for a locale) and says nothing."""
    if not path.is_file():
        return {}
    try:
        catalogue = polib.pofile(str(path))
    except (OSError, ValueError, UnicodeDecodeError) as e:  # polib raises OSError on a syntax error
        print(f"  ! vocabulary: cannot read {path}: {e}", file=sys.stderr)
        return {}
    return {
        e.msgid: e.msgstr
        for e in catalogue
        if e.msgid and e.msgstr.strip() and not (e.obsolete or e.msgctxt or e.msgid_plural)
    }


def _read_csv_words(path: Path) -> dict[str, str]:
    """{bare msgid: translation} of a Frappe `translations/<lang>.csv` (source, translation[, context])."""
    words: dict[str, str] = {}
    if not path.is_file():
        return words
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0] and row[1].strip() and not (len(row) > 2 and row[2].strip()):
                    words[row[0]] = row[1]
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        print(f"  ! vocabulary: cannot read {path}: {e}", file=sys.stderr)
    return words


def _app_words(package_dir: Path, lang: str) -> dict[str, str]:
    """What one app's SOURCE files translate, the way Frappe merges them: the CSV, then the PO on top.

    An identity (the English word said back) is dropped after the merge: it is no translation, it is the
    veto `translate_batch` refuses to write, and it must not stop a real translation being asked for.
    """
    words = _read_csv_words(package_dir / "translations" / f"{lang}.csv")
    words.update(_read_po_words(package_dir / "locale" / f"{lang}.po"))
    return {k: v for k, v in words.items() if v.strip() != k.strip()}


def core_vocabulary(po_path: str, lang: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """({bare msgid: (app, word)}, [apps read]) — what the bench around this PO file already translates.

    The bench is the one the workflow builds: `apps/frappe`, then every dependency app it got next to
    the app being translated (erpnext, payments, neoffice_theme… as `install_apps` says). The app's own
    catalogue is not part of it. Outside a bench (a laptop, a test) nothing is known and everything is
    translated as before. Read from source files: no site, no import of any app, no compiled `.mo`.
    """
    here = Path(po_path).resolve()
    apps_dir = next((d for d in here.parents if d.name == "apps" and (d / "frappe").is_dir()), None)
    if apps_dir is None:
        return {}, []
    target = here.relative_to(apps_dir).parts[0]
    siblings = sorted(
        (d for d in apps_dir.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != target),
        key=lambda d: (d.name != "frappe", d.name),
    )
    vocabulary: dict[str, tuple[str, str]] = {}
    read: list[str] = []
    for app in siblings:
        package = next((d for d in (app / app.name, app / app.name.replace("-", "_")) if d.is_dir()), None)
        if package is None:
            continue
        words = _app_words(package, lang)
        if words:
            read.append(app.name)
        for msgid, word in words.items():
            vocabulary.setdefault(msgid, (app.name, word))
    return vocabulary, read


def _left_to_the_core(entry: polib.POEntry, vocabulary: dict[str, tuple[str, str]]) -> bool:
    """True for an empty bare msgid the core already translates (see the design notes above)."""
    return not (entry.msgctxt or entry.msgid_plural) and entry.msgid in vocabulary


def _left_to_its_context(entry: polib.POEntry, contexted: set[str]) -> bool:
    """True for an empty bare msgid this catalogue also carries with a context (see the design notes above)."""
    return not (entry.msgctxt or entry.msgid_plural) and entry.msgid in contexted


KEEP_EMPTY_FILE = "keep_empty.txt"


def _read_keep_empty(po_path: Path) -> set[str]:
    """The bare msgids the app declares removed on purpose (see the design notes above). A file that is not
    there declares nothing, and neither does one that cannot be read: the declaration is a help, never a
    reason to stop a night."""
    path = po_path.parent / KEEP_EMPTY_FILE
    if not path.is_file():
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! keep-empty: cannot read {path}: {e}", file=sys.stderr)
        return set()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def _left_by_declaration(entry: polib.POEntry, declared: set[str]) -> bool:
    """True for an empty bare msgid the app declares removed on purpose."""
    return not (entry.msgctxt or entry.msgid_plural) and entry.msgid in declared


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    # tolerate a ```json fence or leading prose
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def translate_batch(items: list[str], lang: str, model: str) -> dict[int, str]:
    """items -> {index: translation}, only for verified-safe translations.

    Raises AccountLimitError or ClaudeCallError when the call itself failed (see `_claude`); a call that
    answered, with nothing safe to write, returns {}.
    """
    numbered = [{"i": i, "s": s} for i, s in enumerate(items)]
    prompt = (
        RULES.format(lang_name=LANG_NAMES.get(lang, lang))
        + "\n\nTranslate these items:\n"
        + json.dumps(numbered, ensure_ascii=False)
    )
    out = _parse_json_array(_claude(prompt, model))
    result: dict[int, str] = {}
    for row in out:
        i, t = row.get("i"), row.get("t")
        if not isinstance(i, int) or not isinstance(t, str) or not (0 <= i < len(items)):
            continue
        # Refuse any translation that lost or invented a placeholder/tag.
        if _tokens(t) != _tokens(items[i]):
            print(f"  ~ dropped (token mismatch): {items[i]!r}", file=sys.stderr)
            continue
        if t.strip() == "":
            continue
        # An identity translation is a fleet-wide VETO, not a translation.
        # `get_translations_from_apps` merges every installed app's catalogue in
        # installation order, last one wins -- so a catalogue that answers
        # "System Manager" for "System Manager" does not merely say nothing: it
        # ERASES the real translation another app ships, on every screen of the
        # site. Measured on the dev instance 2026-09-10: 1345 msgids where two
        # installed apps disagree, 43 of them won by an identity, "System
        # Manager" (disputed by 29 apps) among them -- so the whole fleet reads
        # the English (neoffice-maintenance#335).
        #
        # An empty msgstr says the same thing (this app has no opinion) and costs
        # nothing: the screen falls back to the source text either way, and
        # another app's real translation is free to apply. So the model's answer
        # is dropped rather than written, and the entry stays open for a later
        # run that finds a better word.
        if t.strip() == items[i].strip():
            print(f"  ~ dropped (identity, would veto other apps): {items[i]!r}", file=sys.stderr)
            continue
        result[i] = t
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("po_path")
    ap.add_argument("--locale", default="fr")
    ap.add_argument("--model", default=os.environ.get("TRANSLATE_MODEL", "claude-haiku-5-5"))
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--max", type=int, default=int(os.environ.get("TRANSLATE_MAX", "600")),
                    help="cap msgids translated per run (cost guard)")
    args = ap.parse_args()

    if not os.path.exists(args.po_path):
        print(f"no PO file at {args.po_path} — nothing to do")
        return 0

    po = polib.pofile(args.po_path)
    # An empty Language header kills bench build later — set it while here.
    if not (po.metadata.get("Language") or "").strip():
        po.metadata["Language"] = args.locale

    todo = [e for e in po if not e.obsolete and e.msgid and not e.msgstr]

    # A bare msgid the core already translates stays empty (see the design notes): it is not asked, and
    # it does not count against --max.
    vocabulary, sources = core_vocabulary(args.po_path, args.locale)
    left = [e for e in todo if _left_to_the_core(e, vocabulary)]
    left_ids = {id(e) for e in left}
    # So does a bare msgid this catalogue also carries with a context: the app says its sense through the
    # context and a bare entry of its own would replace another app's sense of the word.
    contexted = {e.msgid for e in po if e.msgctxt and e.msgid and not e.obsolete}
    by_context = [e for e in todo if id(e) not in left_ids and _left_to_its_context(e, contexted)]
    left_ids |= {id(e) for e in by_context}
    # And a bare msgid the app declares removed on purpose (keep_empty.txt).
    declared = _read_keep_empty(Path(args.po_path))
    by_declaration = [e for e in todo if id(e) not in left_ids and _left_by_declaration(e, declared)]
    left_ids |= {id(e) for e in by_declaration}
    todo = [e for e in todo if id(e) not in left_ids]
    if left:
        print(f"{os.path.basename(args.po_path)}: {len(left)} untranslated msgid(s) left empty: the desk "
              f"already has their word from {', '.join(sources)} — "
              + ", ".join(f"{e.msgid!r} ({vocabulary[e.msgid][0]})" for e in left[:12])
              + (" …" if len(left) > 12 else ""))
    if by_context:
        print(f"{os.path.basename(args.po_path)}: {len(by_context)} untranslated bare msgid(s) left empty: "
              "this app already says them through a context, and a bare entry of its own would replace the "
              "sense another app gives the word — "
              + ", ".join(repr(e.msgid) for e in by_context[:12])
              + (" …" if len(by_context) > 12 else ""))
    if by_declaration:
        print(f"{os.path.basename(args.po_path)}: {len(by_declaration)} untranslated bare msgid(s) left empty: "
              f"the app declares them removed on purpose ({KEEP_EMPTY_FILE}) — "
              + ", ".join(repr(e.msgid) for e in by_declaration[:12])
              + (" …" if len(by_declaration) > 12 else ""))
    if not todo:
        print(f"{os.path.basename(args.po_path)}: 0 to translate — nothing to do")
        po.save(args.po_path)  # persist the Language header fix if any
        return 0

    todo = todo[: args.max]
    filled = 0
    answered = 0  # calls that got an answer from the model, whatever was safe to write from it
    failed = 0  # calls that did not
    last_error = ""
    for start in range(0, len(todo), args.batch):
        chunk = todo[start : start + args.batch]
        try:
            got = translate_batch([e.msgid for e in chunk], args.locale, args.model)
        except AccountLimitError as e:
            # No later batch can work: stop at the first, keep what this file already holds (and the
            # Language header fix), and FAIL the run once, with the reason, instead of going green.
            po.save(args.po_path)
            print(f"::error::Claude account limit reached: {e}. Nothing more can be translated until it "
                  f"resets; {os.path.basename(args.po_path)} keeps {filled} new translation(s) of "
                  f"{len(todo)} to do. Re-run this workflow after the reset.", flush=True)
            return 1
        except ClaudeCallError as e:
            failed += 1
            last_error = str(e)
            print(f"  ! {e}", file=sys.stderr)
            continue
        answered += 1
        for i, entry in enumerate(chunk):
            if i in got:
                entry.msgstr = got[i]
                filled += 1

    po.save(args.po_path)
    still_empty = len([e for e in po if not e.obsolete and e.msgid and not e.msgstr and id(e) not in left_ids])
    print(f"{os.path.basename(args.po_path)}: filled {filled}/{len(todo)} "
          f"(of {still_empty + filled} to translate) with {args.model}")
    if not answered:
        # Every call failed (no CLI, bad token, a wall of errors): nothing was even attempted for real.
        print(f"::error::every Claude call failed ({failed} batch(es)) and nothing was translated: "
              f"{last_error}", flush=True)
        return 1
    # A partial fill is still progress, and a batch that could not be read stays empty for the next run.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
