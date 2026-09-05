#!/bin/bash
# Rewrite `#////` → `# ////` in every listed repo, comments only, one commit per repo, pushed.
# Each repo is handled in a throwaway worktree of the local clone (the clone itself is untouched).
set -u
CI=/Users/jeremy/GitHub/neoffice-ci/scripts
LOG=${LOG:-/Users/jeremy/.cache/space-rewrite.log}
: > "$LOG"
REPOS=${REPOS:-"frappe:version-15 erpnext:version-15 webshop:version-15 payments:version-15 POSNext:version-15 hrms:version-15 lms:version-15 wiki:version-15 builder:version-15 suite:version-15 neoffice-theme:version-15 erpnextswiss:version-15 neoffice-devops:main"}
for entry in $REPOS; do
  repo=${entry%%:*}; br=${entry##*:}; clone=/Users/jeremy/GitHub/$repo; wt=/Users/jeremy/.cache/space-$repo
  echo "=== $repo ($br) ===" | tee -a "$LOG"
  [ -d "$clone/.git" ] || { echo "  pas de clone" | tee -a "$LOG"; continue; }
  git -C "$clone" fetch -q origin "$br" || { echo "  fetch KO" | tee -a "$LOG"; continue; }
  git -C "$clone" worktree remove -f "$wt" 2>/dev/null; rm -rf "$wt"
  git -C "$clone" worktree add -f --detach "$wt" "origin/$br" >/dev/null 2>&1 || { echo "  worktree KO" | tee -a "$LOG"; continue; }
  python3 "$CI/fork_markers_space.py" --root "$wt" > "$wt/.space.out" 2>&1; tail -1 "$wt/.space.out" | tee -a "$LOG"
  changed=$(git -C "$wt" diff --name-only)
  [ -n "$changed" ] || { echo "  rien à réécrire" | tee -a "$LOG"; git -C "$clone" worktree remove -f "$wt"; continue; }
  # comments-only proof: every removed line must equal its added counterpart once `#////` is spelled `# ////`
  if ! python3 - "$wt" <<'PY'
import re, subprocess, sys
wt = sys.argv[1]
diff = subprocess.check_output(["git", "-C", wt, "diff", "-U0", "--no-color"]).decode("utf-8", "replace")
removed, added = [], []
for line in diff.splitlines():
    if line.startswith("--- ") or line.startswith("+++ "): continue
    if line.startswith("-"): removed.append(line[1:])
    elif line.startswith("+"): added.append(line[1:])
assert len(removed) == len(added), (len(removed), len(added))
# only the comment-opening marker is respelled: a second `#////` quoted inside the comment text stays
bad = [(r, a) for r, a in zip(removed, added) if r.replace("#////", "# ////", 1) != a]
if bad:
    print("NOT A PURE MARKER RESPELL:", bad[:3]); sys.exit(1)
print(f"  preuve: {len(added)} lignes, toutes = respelling du marqueur")
PY
  then echo "  PREUVE KO, abandon" | tee -a "$LOG"; git -C "$clone" worktree remove -f "$wt"; continue; fi
  ok=1; for f in $changed; do case "$f" in *.py) python3 -m py_compile "$wt/$f" 2>>"$LOG" || { echo "  py_compile KO: $f" | tee -a "$LOG"; ok=0; };; esac; done
  [ "$ok" = 1 ] || { git -C "$clone" worktree remove -f "$wt"; continue; }
  (cd "$wt" && echo "$changed" | xargs git add -- && git -c user.name="Jérémy Christillin" -c user.email="jeremy@neoservice.ai" commit -q --no-verify -m "chore(fork): //// markers spelled \"# ////\" (comments only)

ruff/flake8 E265 want a space after the hash; the fork-markers checker
matches the \"////\" substring, so both spellings were valid — this
makes the linters and the convention agree. Rewritten by
neoffice-ci/scripts/fork_markers_space.py, every changed line proven to
be the same comment respelled." ) || { echo "  commit KO" | tee -a "$LOG"; git -C "$clone" worktree remove -f "$wt"; continue; }
  pushed=0
  for i in 1 2 3; do
    if (cd "$wt" && git pull -q --rebase origin "$br" && git push -q origin "HEAD:$br"); then pushed=1; break; fi
    sleep 15
  done
  [ "$pushed" = 1 ] && echo "  poussé: $(git -C "$wt" rev-parse --short HEAD)" | tee -a "$LOG" || echo "  PUSH KO" | tee -a "$LOG"
  git -C "$clone" worktree remove -f "$wt" 2>/dev/null
done
echo "=== terminé ===" | tee -a "$LOG"
