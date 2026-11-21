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

The workflow also refuses a new or touched window of the desk that leaves its model (design system §6.18, drawn by
our fork of Frappe): a `frappe.ui.Dialog` of more than four fields with no `size`, an action labelled « OK »,
« Valider » or « Submit » where it should be named by its verb, a veil or a Bootstrap modal made by hand, a width set
outside `size` (`scripts/check_dialogs.py`; its docstring gives the rules and how to write a reason). The stock blocks
nobody until someone touches it, and `--inventory --json` lists it. At commit time, in `.pre-commit-config.yaml`:

```yaml
      - id: check-dialogs
```

In a repository that holds a `.decision-paths.json` (the paths where its assistant decides, each with its reason), the
workflow refuses a list of everyday words that decides in the assistant's place: a tuple, a table, a pattern with
alternatives or a single word looked for in a text, in a watched file, that is not declared in that file with its
members, what it does and why it is there. A declaration is accepted (who, when) or inherited (the stock found the day
the repository opted in, which nobody accepted, with the issue that follows it); either way the list is frozen: a
declared list that gains a word is refused, it may lose words freely (`scripts/check_word_lists.py`; `--inventory`
lists the stock, `--declare` prints the declarations to write). The stock is judged too: an old list is the model the
next one is copied from. The workflow also reads the commits that touch such a path: each names the site key that
guards the change (`scripts/check_key_line.py`: `Key: <site key>`, or `Key: none, <why nothing changes for a
customer>, reviewed by <who>`), from the day `key_line_since` names; a commit made before `key_line_blocks_from` is
only said as a warning, since a pushed message can no longer be corrected. The line is asked where code changes:
not for a test, not for a change of comments only, not for a path watched for its lists only (`"key_line": false`
on its entry). In a file that mixes what the assistant books with everything else, the entry names the functions
that ask for the line, each with its reason: a commit that changes one of them, or a function of the file they run,
is asked for it. A repository without the file sees nothing change. At commit time, in `.pre-commit-config.yaml`:

```yaml
      - id: check-word-lists
      - id: check-key-line
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

## A targeted fix (`hotfix-overlay.yml`)

One fix, already on the deploy branch and tried, laid on the base an instance already runs, with no wave: the commits are
cherry-picked (`-x`) onto that base, the front end is rebuilt on it when they touch it (the base's own build is the witness that
the build is reproducible: it must give the files the base committed, fingerprints aside), the specs of the front end run, and
the result is published as a TAG `hotfix/<app>/<base7>-<n>`, never a branch. The caller, in the app's repository, is started by
hand with a base, some commits and an opaque identifier (`HF-0001`); it never takes a name of an instance, of a host or of a client,
and the inputs are validated (`scripts/hotfix_overlay.py`) and never written into a shell command.

```yaml
jobs:
  overlay:
    permissions:
      contents: write
    uses: bvisible/neoffice-ci/.github/workflows/hotfix-overlay.yml@<full hash of a neoffice-ci commit>
    with:
      ci_ref: <the same full hash>   # the script is run from it; never a branch
      app: my_app
      base: ${{ inputs.base }}
      commits: ${{ inputs.commits }}
      hotfix_id: ${{ inputs.hotfix_id }}
      front_sources: |      # the sources of the front end: the build runs only when the fix touches one
        frontend/src/
      artifact_paths: |     # everything the build writes and the repository commits
        my_app/public/frontend/
      install_command: cd frontend && yarn install --frozen-lockfile
      build_command: BUILD_TIME_VERSION="$BUILD_VERSION" yarn build   # BUILD_VERSION is set by the workflow
      specs_command: cd frontend && yarn vitest run
```

What the hub does with the tag (judging it against a white list, trying it on a copy of the client's instance, laying it under
maintenance with a way back) is in `bvisible/neoffice-devops`, `neoffice_devops/cloud/hotfix/`. The tag's message and a notice
of the run say which run made which commit (`hotfix-overlay HF-0001 tag=… sha=… run=…`), for the hub to read back. The hub believes a run only if the three files that ran (the caller, the workflow, the script) are versions it knows (`neoffice_devops/cloud/hotfix/trust.py`).
Two jobs: `build` holds a read token (it runs third-party code) and hands over a git bundle, `publish` holds the write token and pushes one annotated tag.
A ruleset of the app's repository keeps a `hotfix/*` tag from being moved.

Source of truth: this file. The fleet tooling (`bvisible/neoffice-devops`) documents the
chain; the tracker is `bvisible/neoffice-maintenance`.

## Licence

MIT, see [LICENSE](LICENSE).
