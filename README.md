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

Source of truth: this file. The fleet tooling (`bvisible/neoffice-devops`) documents the
chain; the tracker is `bvisible/neoffice-maintenance`.
