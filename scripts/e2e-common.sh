# shellcheck shell=bash
# shellcheck disable=SC2154  # root, dir, api and token are the caller's
# Helpers shared by the end-to-end scripts that drive a real install
# (scripts/test-fullstack-e2e.sh, scripts/test-upgrade-e2e.sh). Sourced,
# not run. The caller sets:
#   root   the repository's root
#   dir    the install folder (compose.yml, .env)
#   api    the API's base URL, e.g. http://127.0.0.1:18180
#   token  the API token (after the install, see read_token)

fail () { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass () { printf 'ok: %s\n' "$*"; }
step () { printf '\n--- %s\n' "$*"; }

for tool in curl jq docker sha256sum; do
  command -v "$tool" >/dev/null || fail "$tool is not installed"
done

# test_release DIR VERSION: a stand-in for a GitHub release for the
# installer's OMNISYNC_INSTALL_TEST_RELEASE_DIR (see scripts/install.sh, "CI
# test release"): this checkout's compose file, its SHA256SUMS and the
# version. No image is pulled for it: the images
# ghcr.io/pan-fire/omnisync-{backend,web}:VERSION must exist locally.
test_release () {
  local out=$1 version=$2 image
  for image in backend web; do
    docker image inspect "ghcr.io/pan-fire/omnisync-$image:$version" >/dev/null \
      || fail "build ghcr.io/pan-fire/omnisync-$image:$version first"
  done
  rm -rf "$out"
  mkdir -p "$out"
  cp "$root/deploy/compose.yml" "$out/compose.yml"
  printf '%s\n' "$version" >"$out/VERSION"
  (cd "$out" && sha256sum compose.yml >SHA256SUMS)
}

dc () { docker compose --project-directory "$dir" -f "$dir/compose.yml" "$@" </dev/null; }

read_token () {
  token=$(grep '^OMNISYNC_API_TOKEN=' "$dir/.env" | cut -d= -f2-)
  [ "${#token}" -ge 32 ] || fail "no API token in $dir/.env"
}

# For the caller's EXIT trap: the backend's last log lines, then
# `compose down --volumes`.
teardown () {
  if [ -f "$dir/compose.yml" ]; then
    printf '\n--- backend log (last 40 lines) ---\n'
    dc logs --tail 40 backend || true
    dc down --volumes >/dev/null 2>&1 || true
  fi
}

# call METHOD PATH [JSON]: one API request with the token. Sets $status (the
# HTTP status) and $body (the answer).
call () {
  local out data=()
  if [ -n "${3:-}" ]; then data=(-H 'Content-Type: application/json' --data "$3"); fi
  out=$(curl -sS -X "$1" "$api$2" -H "Authorization: Bearer $token" "${data[@]}" -w '\n%{http_code}') \
    || fail "$1 $2: no answer"
  status=${out##*$'\n'}
  body=${out%$'\n'*}
}

# api METHOD PATH [JSON] CODE...: call, and fail unless the status is one of CODE.
api () {
  local method=$1 path=$2 payload="" code
  shift 2
  case ${1:-} in '{'*|'['*) payload=$1; shift ;; esac
  call "$method" "$path" "$payload"
  for code in "$@"; do [ "$status" = "$code" ] && return 0; done
  fail "$method $path answered $status (expected $*): $body"
}

# field FILTER: a jq filter applied to $body (raw output).
field () { jq -r "$1" <<<"$body"; }

# wait_until SECONDS WHAT CMD...: polls CMD every half second until it
# succeeds; fails with WHAT after SECONDS.
wait_until () {
  local limit=$1 what=$2 deadline=$((SECONDS + $1))
  shift 2
  until "$@"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "waited ${limit}s for $what"
    sleep 0.5
  done
}

_job_done () { call GET "/jobs/$1"; [ "$status" = 200 ] && [ "$(field .finished_at)" != null ]; }

_idle () {
  call GET "/profiles/$1/sync/status"
  [ "$status" = 200 ] && [ "$(field .current_job_id)" = null ] \
    && [[ $(field .state) == idle || $(field .state) == error ]]
}

# wait_idle SLUG: until no sync of the profile runs.
wait_idle () { wait_until 120 "profile $1 to be idle" _idle "$1"; }

# sync_now SLUG DIRECTION [FORCE]: starts a sync and waits for its job;
# $body is then the finished job.
sync_now () {
  local job
  api POST "/profiles/$1/sync/start" "{\"direction\": \"$2\", \"force\": ${3:-false}}" 200 202
  job=$(field .job_id)
  wait_until 120 "sync job $job" _job_done "$job"
}

# add_local_remote NAME: an rclone remote of type local, so that a profile
# can sync two folders of the host. The API refuses local remotes on
# purpose (a profile names its local folder directly), so it goes into
# rclone.conf the way an operator would add it: with rclone in the
# container, as the user OmniSync runs as.
add_local_remote () {
  dc exec -T --user "$(id -u):$(id -g)" backend \
    rclone --config /data/omnisync/rclone.conf config create "$1" local >/dev/null \
    || fail "could not add the local remote $1"
}

# same_tree EXPECTED DIR: DIR holds exactly the files of EXPECTED, byte for
# byte (the sync marker and the trash folder aside).
same_tree () {
  diff -r --exclude=.omnisync-check --exclude=.omnisync-trash "$1" "$2" >&2 \
    || fail "$2 differs from $1 (see above)"
}

# sql QUERY: rows from the backend's database (read-only), '|'-separated.
sql () {
  dc exec -T --user "$(id -u):$(id -g)" backend python -c '
import sqlite3, sys
db = sqlite3.connect("file:/data/omnisync/omnisync.db?mode=ro", uri=True)
print("\n".join("|".join(map(str, row)) for row in db.execute(sys.argv[1])))' "$1"
}

# audit_has PATTERN: the audit trail (GET /logs?category=audit) has an entry
# matching the extended regular expression PATTERN.
audit_has () {
  api GET '/logs?category=audit&limit=200' 200
  field '.[].message' | grep -E -- "$1" >/dev/null || fail "no audit entry matches: $1"
}
