#!/usr/bin/env python3
"""Headless PO translator — fills empty msgstr with Sonnet, in CI.

The manual half of the pipeline already exists (translate.sh does
generate-pot-file / update-po-files / compile-po-to-mo; the /translate skill does
the AI step interactively). This is the missing piece: a NON-interactive
translator the CI runs on every push, so new user-facing strings get translated
without anyone opening a session.

Design, on purpose:
- Only ever fills entries whose msgstr is empty. A human translation is never
  overwritten, and a re-run is a no-op — safe to run on every push.
- Placeholders, format specifiers and HTML are preserved verbatim (the model is
  told, and we verify every returned string still carries them; a mismatch is
  dropped, never written).
- The model is Sonnet, billed to the subscription via the `claude` CLI print
  mode (CLAUDE_CODE_OAUTH_TOKEN) — cheap, and translation is volume not
  reasoning (fleet rule: never a bigger model for translation).
- Fixes the PO `Language:` header while here (an empty one kills `bench build`).

Exit 0 whether or not anything changed; prints a one-line summary. Never raises
into the workflow on a single batch failure — it logs and moves on, leaving those
msgids empty for the next run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

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


def _tokens(s: str) -> list[str]:
    return sorted(_PLACEHOLDER.findall(s))


def _claude(prompt: str, model: str) -> str:
    """Call the Claude CLI in print mode. Returns the model's text or ''."""
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:  # noqa: BLE001
        print(f"  ! claude CLI call failed: {e}", file=sys.stderr)
        return ""
    if proc.returncode != 0:
        print(f"  ! claude exited {proc.returncode}: {proc.stderr[:200]}", file=sys.stderr)
        return ""
    try:
        return json.loads(proc.stdout).get("result", "") or ""
    except json.JSONDecodeError:
        return proc.stdout  # some versions print the text directly


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
    """items -> {index: translation}, only for verified-safe translations."""
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
        result[i] = t
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("po_path")
    ap.add_argument("--locale", default="fr")
    ap.add_argument("--model", default=os.environ.get("TRANSLATE_MODEL", "claude-sonnet-5"))
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
    if not todo:
        print(f"{os.path.basename(args.po_path)}: 0 untranslated — nothing to do")
        po.save(args.po_path)  # persist the Language header fix if any
        return 0

    todo = todo[: args.max]
    filled = 0
    for start in range(0, len(todo), args.batch):
        chunk = todo[start : start + args.batch]
        got = translate_batch([e.msgid for e in chunk], args.locale, args.model)
        for i, entry in enumerate(chunk):
            if i in got:
                entry.msgstr = got[i]
                filled += 1

    po.save(args.po_path)
    print(f"{os.path.basename(args.po_path)}: filled {filled}/{len(todo)} "
          f"(of {len([e for e in po if not e.obsolete and e.msgid and not e.msgstr]) + filled} "
          f"untranslated) with {args.model}")
    # non-zero would fail the workflow; a partial fill is still progress.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
