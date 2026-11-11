#!/usr/bin/env bash
# Install Ubuntu packages on a GitHub runner, and get through an outage of the archives its apt sources name.
# The install step of .github/workflows/frappe-app-ci.yml fetches it next to ci_retry.sh and runs it:
#
#     bash ~/ci_apt_install.sh mariadb-client xfonts-75dpi xfonts-base
#
# Why: on 2026-11-11 from about 13:40 UTC the runners stopped resolving the Ubuntu archives of their own apt sources
# (azure.archive.ubuntu.com, archive.ubuntu.com, security.ubuntu.com: "Temporary failure resolving") while github.com
# and the rest still answered, and every app CI died in this install for hours, before one test ran
# (neoffice-maintenance#2542). The step retried `apt-get update` once, the install not at all, and had nowhere else to go.
#
# What it does:
#   1. every apt call retries each download (Acquire::Retries) and gives up on a silent host after a timeout; the
#      update fails on any index it could not fetch (--error-on=any): otherwise apt carries on without its lists and
#      only the install fails, later and with a vaguer message;
#   2. when every host the runner's sources name resolves, update + install go through `retry` (ci_retry.sh: three
#      attempts, 30 s then 60 s apart);
#   3. when one of them does not resolve, or those attempts all fail, apt is pointed, for the rest of the job, at the
#      first official Ubuntu mirror of CI_APT_MIRRORS that resolves and serves this release, and tries again there.
# Nothing is trusted less: a mirror serves the same archive, signed by the same key, and apt checks that signature
# against the keyring the runner's own sources name (their Signed-By), exactly as it did before.
# A real error (a package that does not exist) fails every attempt and every mirror, and still fails the step.
#
# CI_APT_DIR, CI_OS_RELEASE, CI_APT_MIRRORS and CI_APT_RESOLVE_PAUSE exist for scripts/test_ci_apt_install.py.
set -u

APT_DIR="${CI_APT_DIR:-/etc/apt}"
OS_RELEASE="${CI_OS_RELEASE:-/etc/os-release}"
# Official mirrors of the whole archive (security pocket included) run by others than the runner's provider:
# kernel.org's, then two university and network mirrors in Switzerland and Germany.
MIRRORS="${CI_APT_MIRRORS:-http://mirrors.edge.kernel.org/ubuntu http://mirror.init7.net/ubuntu http://ftp.halifax.rwth-aachen.de/ubuntu}"
RESOLVE_PAUSE="${CI_APT_RESOLVE_PAUSE:-5}"

here=$(cd "$(dirname "$0")" && pwd)
if [ -r "$here/ci_retry.sh" ]; then
  # shellcheck source=/dev/null
  . "$here/ci_retry.sh"
else
  retry() { "$@"; }
fi
export CI_RETRY_DELAY="${CI_RETRY_DELAY:-30}"

# shellcheck source=/dev/null
. "$OS_RELEASE"
codename="${VERSION_CODENAME:?no VERSION_CODENAME in $OS_RELEASE}"

APT_OPTS=(-q -o Acquire::Retries=5 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)

# The files that hold the runner's active Ubuntu sources: the deb822 files and the one-line lists.
source_files() {
  local f
  for f in "$APT_DIR"/sources.list "$APT_DIR"/sources.list.d/*.list "$APT_DIR"/sources.list.d/*.sources; do
    [ -f "$f" ] && echo "$f"
  done
  return 0
}

active_lines() {
  local f
  source_files | while read -r f; do grep -v '^[[:space:]]*#' "$f"; done
}

# The hosts apt will ask: those the sources name, and those of the mirror lists they point at
# (`URIs: mirror+file:/etc/apt/apt-mirrors.txt` on the runner images). A mirror list no source points at any more
# (after use_mirror) is not read.
source_hosts() {
  local lists list
  lists=$(active_lines | grep -oE 'mirror\+file:[^[:space:]]+' | sed 's#^mirror+file:##' | sort -u)
  {
    active_lines
    for list in $lists; do [ -f "$list" ] && grep -v '^[[:space:]]*#' "$list"; done
  } | grep -oE 'https?://[^/[:space:]]+' | sed -E 's#^https?://##; s#:[0-9]+$##' | sort -u
}

# A name that does not resolve three times, a few seconds apart, is not a blip.
resolves() {
  local i
  for i in 1 2 3; do
    timeout 10 getent hosts "$1" >/dev/null 2>&1 && return 0
    [ "$i" -lt 3 ] && sleep "$RESOLVE_PAUSE"
  done
  return 1
}

first_dead_host() {
  local h
  for h in $(source_hosts); do
    resolves "$h" || { echo "$h"; return 0; }
  done
  return 0
}

answers() {
  curl -sf -o /dev/null --max-time 20 "$1/dists/$codename/InRelease"
}

# The keyring and components of the runner's own Ubuntu sources, so a mirror is held to the same signature.
first_field() {
  local value
  value=$(active_lines | sed -n "s/^$1:[[:space:]]*//p" | head -1)
  echo "${value:-$2}"
}

use_mirror() {
  local keyring components aside f
  keyring=$(first_field Signed-By /usr/share/keyrings/ubuntu-archive-keyring.gpg)
  components=$(first_field Components "main restricted universe multiverse")
  aside="$APT_DIR/sources-of-the-runner"
  sudo mkdir -p "$aside"
  for f in "$APT_DIR"/sources.list "$APT_DIR"/sources.list.d/*.list "$APT_DIR"/sources.list.d/*.sources; do
    [ -f "$f" ] && sudo mv "$f" "$aside/"
  done
  printf 'Types: deb\nURIs: %s\nSuites: %s %s-updates %s-backports %s-security\nComponents: %s\nSigned-By: %s\n' \
    "$1" "$codename" "$codename" "$codename" "$codename" "$components" "$keyring" \
    | sudo tee "$APT_DIR/sources.list.d/ubuntu-mirror.sources" >/dev/null
  echo "::warning::apt uses $1 for the rest of this job: the runner's own Ubuntu archives do not answer (the runner's sources are in $aside)"
}

# `retry` sees the status of the last command of the function: the && list below.
# The update is cut after 8 minutes (killing it harms nothing); the install is not (it may be inside dpkg).
update_and_install() {
  sudo timeout 480 apt-get "${APT_OPTS[@]}" update --error-on=any && sudo apt-get "${APT_OPTS[@]}" install -y "$@"
}

main() {
  local dead m
  if [ "$#" -eq 0 ]; then
    echo "usage: ci_apt_install.sh <package or .deb>..." >&2
    return 2
  fi
  dead=$(first_dead_host)
  if [ -z "$dead" ]; then
    retry update_and_install "$@" && return 0
    echo "::warning::apt failed from the archives of the runner's sources: trying a mirror"
  else
    echo "::warning::the runner does not resolve $dead, which its apt sources name: trying a mirror"
  fi
  for m in $MIRRORS; do
    if ! resolves "$(echo "$m" | sed -E 's#^https?://##; s#[:/].*##')" || ! answers "$m"; then
      echo "::warning::$m does not answer from this runner either"
      continue
    fi
    use_mirror "$m"
    CI_RETRY_ATTEMPTS=2 retry update_and_install "$@" && return 0
  done
  echo "::error::no Ubuntu archive answered from this runner (its own, then: $MIRRORS)"
  return 1
}

main "$@"
