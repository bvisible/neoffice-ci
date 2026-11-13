# neoffice-ci

The reusable GitHub Actions workflow every Neoffice fleet app calls for its CI.
It lives in a **public** repository on purpose: GitHub only lets public callers
use reusable workflows hosted in public repositories, and the fleet mixes
private apps with public forks. **Nothing sensitive lives here** — the recipe
(bench init on the fleet's frappe fork, dependency apps, blocking build,
`bench run-tests`, report to the hub) carries no secret; each caller passes its
own via `secrets: inherit`.

## Usage (in an app repository, `.github/workflows/tests.yml`)

```yaml
jobs:
  ci:
    uses: bvisible/neoffice-ci/.github/workflows/frappe-app-ci.yml@main
    with:
      app: my_app                       # python package name
      install_apps: "erpnext,payments"  # dependency apps, bvisible forks
    secrets: inherit                    # CI_REPORT_SECRET, FLEET_READ_TOKEN
```

Inputs: `app`, `install_apps`, `python-version`, `test_args` (`--debug-records[:Doctype]`
walks `make_test_records` with a spy), `pytest_paths` (informative pytest pass for
module-level suites), `upstream_preview` (bench on upstream `frappe/frappe` +
`frappe/erpnext` instead of the forks — measures the cost of the pending fork upgrade).

Secrets expected from the caller: `CI_REPORT_SECRET` (report to the hub, which opens /
closes the repo's `[CI]` issue in the fleet tracker) and `FLEET_READ_TOKEN` (read access
to the private bvisible dependency repositories).

The workflow refuses a new test that creates or submits a business document as Administrator
(`scripts/check_test_roles.py`, the tool to act as a real role being `frappe/tests/roles.py` of the fork). The same
check runs at commit time from `.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/bvisible/neoffice-ci
    rev: main
    hooks:
      - id: check-test-roles
```

The workflow also refuses a new or changed `@frappe.whitelist` method that holds no permission check, a new
`allow_guest=True` that does not say why, and a parameter with no annotation that reaches a query as a filter
(`scripts/check_whitelist_permissions.py`; its docstring gives the rule and how to write a reason). The stock blocks
nobody until someone touches it. At commit time, in `.pre-commit-config.yaml`:

```yaml
      - id: check-whitelist-permissions
```

A push whose files, since the last green run of its branch, are all documentation or translation catalogues
(`*.md`, `docs/**`, `.claude/**`, `**/locale/*.po|pot`) does not run the heavy suite: a light job reads its
catalogues the way `bench compile-po-to-mo` does (`scripts/ci_light_change.py`, `scripts/check_po_reads.py`). Any
doubt — no green run known, a forced push, a re-run, a list the API may have cut — runs the suite, and a skipped push
reports nothing, so the next nightly still runs the whole suite on it.

A red run's report names what failed: `scripts/ci_test_summary.py` reads the logs of the server suite, of the
blocking pytest pass and of `node --test`, and the hub posts on the repository's `[CI]` issue each suite's verdict
and its failing tests by name (never a traceback, an error message or a parameter). The end of the log is sent only
when no failing test is found, or when the script cannot run.

Source of truth: this file. The fleet tooling (`bvisible/neoffice-devops`) documents the
chain; the tracker is `bvisible/neoffice-maintenance`.

## Licence

MIT, see [LICENSE](LICENSE).
