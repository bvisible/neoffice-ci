# Where these rules come from, and how to refresh them

Upstream: <https://github.com/frappe/semgrep-rules> (branch `develop`), vendored
at commit `b101a16` (2026-08-05, "Warn about enqueue without enqueue_after_commit").

**They are tracked here on purpose.** They used to be a gitignored clone of the
repository under its old name, frozen at `d01e207` (2025-03-21). A fresh clone of
this app therefore had no rules at all and could not commit — `semgrep` exits 7
on a missing `--config` path — while the machines that happened to have the
directory were running an 18-month-old set: **34 rules out of 49, with the whole
`security/` directory missing**. A rule nobody can run is not a rule.

Only the `.yml` rule files are kept. Upstream also ships the fixtures it tests
its own rules against — deliberately bad code, which black, flake8 and semgrep
itself would all trip over if it were tracked here. We consume these rules; we do
not develop them.

## Refreshing

```bash
git clone --depth 1 https://github.com/frappe/semgrep-rules /tmp/sgup
rsync -a --delete --include='*/' --include='*.yml' --exclude='*' \
      /tmp/sgup/rules/ .semgrep-rules/rules/
```

Then **measure before you enforce** — the count is the decision:

```bash
semgrep --metrics=off --config=.semgrep-rules/rules --json neoffice_theme \
  | python3 -c 'import json,sys,collections; print(*collections.Counter(r["check_id"] for r in json.load(sys.stdin)["results"]).most_common(), sep="\n")'
```

## What is enforced, and what is not

The pre-commit hook runs the whole set **minus the rules listed in its
`--exclude-rule` arguments**, each carrying the count that justifies it, measured
on `neoffice_theme`. Those are debt, not decisions: an excluded rule is one we
have not paid yet, and the number is what it costs.

Two things to know before touching either list:

- **`--exclude-rule` wants the full id**, the one semgrep prints —
  `semgrep-rules.rules.security.frappe-ssti`, not `frappe-ssti`. The short form
  is accepted silently and excludes nothing.
- **The same is true of `# nosemgrep:`**, and it must sit on the line of the
  match or the one immediately above it. Anything else suppresses nothing, and
  says nothing about it.

See neoffice-maintenance#329.
