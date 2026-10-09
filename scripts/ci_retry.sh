# Retry a command a few times. Sourced by the steps of .github/workflows/frappe-app-ci.yml:
#
#     . ~/ci_retry.sh
#     retry bench get-app ...
#
# Why: `bench init` and `bench get-app` download from GitHub, PyPI and the yarn registry, and a registry that answers
# "502 Bad Gateway" for a minute failed the whole run of an app whose code nobody had touched (09.10.2026, `yarn
# install`, a push of one of our apps): a red CI, an issue on the tracker, a morning spent on nothing. A real error
# (a commit that does not install) fails every attempt the same way and still fails the run, a little later.
#
# CI_RETRY_ATTEMPTS (default 3) and CI_RETRY_DELAY (seconds before the second attempt, default 20, doubled after each
# failure) exist so that the tests do not wait. Only the first word of the command is printed: the arguments of a
# `bench get-app` carry an access token.
retry() {
  local attempts="${CI_RETRY_ATTEMPTS:-3}" delay="${CI_RETRY_DELAY:-20}" attempt=1 status
  until "$@"; do
    status=$?
    if [ "$attempt" -ge "$attempts" ]; then
      echo "::error::'$1' failed $attempt times, giving up"
      return "$status"
    fi
    echo "::warning::attempt $attempt of '$1' failed (exit $status), trying again in ${delay}s"
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}
