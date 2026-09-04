You are adding `//// Neoffice` marker comments in the fork **{repo}** (branch `{branch}`). The working directory is the repository root, checked out at `{head}`, right after the push `{range}`.

## The rule (absolute)
Every change Neoffice makes to code we did not write carries a marker comment that states WHY: what upstream does, what broke or was missing, what we do instead, and — for a workaround — when to drop it. At the next upstream merge, `grep -rn "////"` must map our whole divergence.

## Your job
For each unmarked hunk listed below, insert a marker comment immediately above the changed lines (or above the block they belong to), with the reason taken from the commit message(s) of the range. Read the surrounding code and, if useful, the diff, to describe the change precisely. **Comments only.**

## Hard constraints
- Add comment lines only. Never add, edit, move, reindent or delete any non-comment line. Never reformat a file.
- Syntax by language: Python, YAML, TOML, shell → `#//// Neoffice — …` · JS, TS, Vue `<script>`, SCSS → `//// Neoffice — …` · plain CSS → `/* //// Neoffice — … */` · Jinja templates (`templates/`, `www/`) → `{# //// Neoffice — … #}` · plain HTML / Vue `<template>` → `<!-- //// Neoffice — … -->`. Match the indentation of the line below the marker.
- Never inside a string, a template literal, an HTML attribute, a `<script>`/`<style>` block (use the JS/CSS syntax there), a JSON value, or between a decorator and its `def`.
- One marker per hunk. For a block longer than about 30 lines open with `//// Neoffice ▼▼▼ — …` and close with `//// Neoffice ▲▲▲` (same comment syntax as the file).
- For a hunk that only removed lines, put the marker where the lines were: `//// Neoffice — removed <what> (sha "subject"): <why>`.
- The reason comes from the commit message. Cite the short sha and subject: `(abc1234 "subject")`. When the message states no usable reason, write `//// Neoffice — TO REVIEW: "<subject>" (sha) — reason not stated in the commit`. Never invent a reason.
- A marker only covers a hunk when it stands INSIDE the hunk or within the 3 lines right above its first line: the checker looks no further. A block marker 10 lines up, or on the enclosing function, does NOT cover it — add a one-line pointer marker right above the hunk (`//// Neoffice — see the block marker above: <3-word reason>`). Never answer "already covered" for a hunk the checker lists: it is listed precisely because nothing is within 3 lines.
- Files that cannot carry comments (JSON, images, .po/.mo, lockfiles): do not edit them. Instead append one line per file to `NEOFFICE_FORK_MARKERS.md` at the repository root — create it with the title `# Neoffice fork markers` if missing — under a `## Auto-marked (fork-markers workflow)` heading: `- \`path\` — <what changed, e.g. the fields added> — <reason> (sha "subject")`.
- In `.html`/Jinja templates never write the sequence `.__` (dot + two underscores), `__class__`, `{{` or `{%` inside a marker: Frappe's `safe_render` refuses the whole page (HTTP 417) when its source contains them, comments included.
- Do not touch anything not listed. Do not run git. English only inside files. When done, answer with one line per file you changed.

## Commits of the range (the source of the reasons)
{commits}

## Unmarked hunks
{hunks}
