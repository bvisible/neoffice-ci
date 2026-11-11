#!/usr/bin/env python3
"""Does each catalogue still read the way Frappe reads it? The light check of a push that changes only translations.

A push whose files are all documentation and translations no longer runs the heavy suite (ci_light_change.py).
What stands in for it on a catalogue is what Frappe itself does with one: `bench compile-po-to-mo` opens the
.po with Babel's `read_po` and writes it with `write_mo` (frappe/gettext/translate.py). That reader is lenient on
purpose: it shrugs at a duplicated entry or a placeholder that differs, which `msgfmt --check` refuses — and two
such entries of erpnextswiss have been served without harm for months. So the check is Babel's reader, not
msgfmt's: a catalogue fails here only when Frappe could not compile it either (a file that is not text in its
declared charset, an entry Babel cannot parse).

  check_po_reads.py FILE.po [FILE.pot ...]

Exit 1 when one of them does not read or does not compile; 0 otherwise (and with no file at all).
"""
from __future__ import annotations

import io
import sys


def problem(path: str) -> str | None:
    """None when the catalogue reads and compiles as in Frappe, else why not."""
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po

    try:
        with open(path, "rb") as f:
            catalog = read_po(f)
        write_mo(io.BytesIO(), catalog)
    except FileNotFoundError:
        return "the file does not exist at this commit"
    except Exception as e:  # noqa: BLE001 — whatever stops Babel stops `bench compile-po-to-mo` too
        return f"{type(e).__name__}: {str(e)[:200]}"
    return None


def main(paths: list[str]) -> int:
    try:
        import babel  # noqa: F401
    except ImportError:
        print("::error::Babel is not installed: the catalogues cannot be read as Frappe reads them")
        return 1
    failed = 0
    for path in paths:
        why = problem(path)
        if why:
            failed += 1
            print(f"::error file={path}::this catalogue does not read as Frappe reads it: {why}")
        else:
            print(f"ok      {path}")
    print(f"{len(paths)} catalogue(s) read, {failed} refused")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
