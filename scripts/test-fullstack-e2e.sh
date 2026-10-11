#!/usr/bin/env bash
# OmniSync doing its job, end to end: the images built from this checkout,
# installed with scripts/install.sh (CI job "installer" in
# .github/workflows/ci.yml), driven through the public API with the API
# token, with every result checked on disk. Not for users.
#
#   docker build -t ghcr.io/pan-fire/omnisync-backend:9999.0.0-ci .
#   docker build -t ghcr.io/pan-fire/omnisync-web:9999.0.0-ci frontend
#   scripts/test-fullstack-e2e.sh 9999.0.0-ci
#
# In a temporary folder and on non-default ports (API_PORT, WEB_PORT;
# default 18180 and 13180) it installs OmniSync with the web UI login on,
# then, through the API: a two-way profile between two folders inside the
# sync folder; a sync, checked byte for byte; edits on both sides, the
# conflict in /conflicts and its resolution; a mass deletion beyond the
# profile's delete limit, refused with no file lost; a backup and a restore
# from it; and the audit trail (GET /logs) of all of that.
#
# With E2E_BROWSER=1 it also runs the real-browser smoke
# (frontend/playwright.stack.config.ts) while the conflict is open: it
# needs `pnpm install` and `pnpm exec playwright install chromium` in
# frontend/ first. Everything it created is removed at the end, also when a
# check fails.

set -euo pipefail

version=${1:?usage: $0 VERSION (the tag of the locally built images)}
root=$(cd "$(dirname "$0")/.." && pwd)
api_port=${API_PORT:-18180}
web_port=${WEB_PORT:-13180}
project=${PROJECT_NAME:-omnisync-fullstack-e2e}
work=$(mktemp -d "${TMPDIR:-/tmp}/omnisync-fullstack.XXXXXX")
dir="$work/install" sync="$work/sync" expected="$work/expected"
local_dir="$sync/docs" remote_dir="$sync/docs-remote" backups="$sync/backups"
api="http://127.0.0.1:$api_port" web="http://127.0.0.1:$web_port"
token=""
# shellcheck source=scripts/e2e-common.sh
. "$root/scripts/e2e-common.sh"

cleanup () { teardown; rm -rf "$work"; }
trap cleanup EXIT

test_release "$work/release" "$version"
export OMNISYNC_INSTALL_TEST_RELEASE_DIR="$work/release"

# --- install, with the web UI login on ----------------------------------------
step "install $version"
bash "$root/scripts/install.sh" install --dir "$dir" --yes --sync-dir "$sync" \
  --api-port "$api_port" --web-port "$web_port" --project-name "$project" \
  --no-ui-password --health-timeout 240 </dev/null
read_token
# The login as the README's "Built-in login" sets it without a terminal.
password=$(openssl rand -hex 16)
printf 'OMNISYNC_UI_PASSWORD=%s\nOMNISYNC_UI_SESSION_SECRET=%s\n' "$password" "$(openssl rand -hex 32)" >>"$dir/.env"
dc up -d --quiet-pull
login_required () { [ "$(curl -s "$web/api/profiles" | jq -r .code 2>/dev/null)" = login_required ]; }
wait_until 60 "the web UI to ask for its login" login_required
pass "installed; the web UI asks for its login"

# --- a profile between two folders of the sync folder ------------------------
step "profile"
add_local_remote e2elocal
mkdir -p "$expected/docs" "$local_dir" "$remote_dir" "$backups"
printf 'first version\n' >"$expected/a.txt"
head -c 307200 /dev/urandom >"$expected/docs/report.bin"
printf 'Grüße, 你好\n' >"$expected/docs/notes ü.txt"
cp -a "$expected/." "$local_dir/"
printf 'made on the other side\n' >"$expected/from-remote.txt"
cp "$expected/from-remote.txt" "$remote_dir/"

# No automatic run gets in the way: the watcher waits an hour after a
# change, the scheduler a week. The delete limit is the profile's own.
api POST /profiles "$(jq -n --arg l "$local_dir" --arg r "e2elocal:$remote_dir" \
  '{name: "E2E Docs", local_dir: $l, remote_dir: $r, debounce_seconds: 3600,
    pull_interval_minutes: 10080, rclone_args: ["--max-delete", "3"]}')" 201
slug=$(field .slug)
[ "$(field .sync_mode)" = two_way ] || fail "a new profile is not two-way: $body"
# A new profile's engine starts with a resync (the union of both folders).
wait_idle "$slug"
api GET "/profiles/$slug/jobs" 200
[ "$(field '[.[] | select(.direction == "resync" and .status == "completed")] | length')" = 1 ] \
  || fail "no completed first resync: $body"
pass "profile $slug created; its first run was a resync"

# --- a sync, byte for byte --------------------------------------------------------
step "sync"
sync_now "$slug" two_way
[ "$(field .status)" = completed ] || fail "the two-way sync did not complete: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
cmp -s "$local_dir/.omnisync-check" "$remote_dir/.omnisync-check" || fail "the sync markers differ"
pass "both folders hold the same files, byte for byte"

# --- a conflict ----------------------------------------------------------------------
step "conflict"
# Fixed modification times: the remote edit is the newer one.
printf 'local edit\n' >"$local_dir/a.txt"
touch -d '2001-01-01 00:00:00Z' "$local_dir/a.txt"
printf 'remote edit\n' >"$remote_dir/a.txt"
touch -d '2001-01-02 00:00:00Z' "$remote_dir/a.txt"
sync_now "$slug" two_way
[ "$(field .status) $(field .conflicts)" = "completed 1" ] || fail "no conflict recorded: $body"
# Both versions are kept on both sides: the newer under the name.
printf 'remote edit\n' >"$expected/a.txt"
printf 'local edit\n' >"$expected/a.local-conflict1.txt"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
api GET "/conflicts?profile=$slug" 200
[ "$(field 'length')" = 1 ] || fail "/conflicts does not list one conflict: $body"
[ "$(field '.[0] | "\(.file_path) \(.local_kept_as) \(.remote_kept_as) \(.resolved)"')" \
  = "a.txt a.local-conflict1.txt a.txt false" ] || fail "unexpected conflict: $body"
conflict=$(field '.[0].id')
pass "the conflict on a.txt is in /conflicts; both versions are kept"

if [ "${E2E_BROWSER:-0}" = 1 ]; then
  step "the web UI in a real browser"
  playwright="$root/frontend/node_modules/.bin/playwright"
  [ -x "$playwright" ] || fail "run pnpm install in frontend/ first"
  (cd "$root/frontend" && STACK_URL="$web" STACK_PASSWORD="$password" STACK_PROFILE="E2E Docs" \
    STACK_SLUG="$slug" STACK_CONFLICT=a.txt "$playwright" test -c playwright.stack.config.ts)
  wait_idle "$slug"
  # The browser's sync went through the web UI's server to the backend.
  audit_has "^sync\.start profile=$slug direction=two_way force=false outcome=ok client=.* via="
  same_tree "$expected" "$local_dir"
  same_tree "$expected" "$remote_dir"
  pass "the browser logged in, saw the profile and the conflict, and started a sync"
fi

api POST "/conflicts/$conflict/resolve" '{"resolution": "keep_local"}' 200
[ "$(field '"\(.resolved) \(.resolution)"')" = "true keep_local" ] || fail "not resolved: $body"
printf 'local edit\n' >"$expected/a.txt"
rm "$expected/a.local-conflict1.txt"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
grep -rqx 'remote edit' "$remote_dir/.omnisync-trash" || fail "the dropped version is not in the remote's trash"
sync_now "$slug" two_way
[ "$(field .status) $(field .conflicts)" = "completed 0" ] || fail "the sync after the resolution: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
api GET "/conflicts?profile=$slug" 200
[ "$(field 'length')" = 0 ] || fail "the conflict is still listed: $body"
pass "keep_local: the local version on both sides, the other in the trash"

# --- a mass deletion beyond the delete limit -----------------------------------------
step "delete limit"
mkdir -p "$expected/many"
for i in 1 2 3 4 5; do head -c 1000 /dev/urandom >"$expected/many/f$i.bin"; done
cp -a "$expected/many" "$local_dir/"
sync_now "$slug" two_way
[ "$(field .status)" = completed ] || fail "the sync of many/: $body"
same_tree "$expected" "$remote_dir"

rm "$local_dir"/many/f*.bin
sync_now "$slug" two_way
[ "$(field .status)" = failed ] || fail "a sync deleting 5 files past the limit of 3 ran: $body"
api GET "/profiles/$slug/sync/status" 200
[ "$(field .intervals_paused)" = true ] || fail "the profile is not paused: $body"
[[ $(field .last_error) == *"5 file(s) in the remote folder, more than the limit of 3"* ]] \
  || fail "the refusal does not say why: $body"
same_tree "$expected" "$remote_dir"
[ ! -d "$remote_dir/.omnisync-trash/many" ] || fail "files went to the remote's trash"
api POST "/profiles/$slug/sync/start" '{"direction": "two_way"}' 409
[ "$(field .code)" = sync_paused ] || fail "a sync of the paused profile: $body"
# Recover the deleted files the way the refusal suggests: from the other side.
sync_now "$slug" pull true
[ "$(field .status)" = completed ] || fail "the forced pull: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
pass "the mass deletion was refused and paused the profile; no file was lost"

# --- backup and restore ------------------------------------------------------------------
step "backup and restore"
api POST "/profiles/$slug/backups" "$(jq -n --arg p "$backups" \
  '{name: "E2E backup", target_type: "local", target_path: $p}')" 201
target=$(field .id)
backup_done () {
  call GET "/profiles/$slug/backups/$target/jobs/$1"
  [ "$status" = 200 ] && [ "$(field .status)" != running ]
}
api POST "/profiles/$slug/backups/$target/run" 202
job=$(field .id)
wait_until 120 "backup job $job" backup_done "$job"
[ "$(field .status) $(field .verify_status)" = "completed verified" ] || fail "the backup: $body"
snapshot=$(field .snapshot_id)
api GET "/profiles/$slug/backups/$target/snapshots" 200
[ "$(field ".[] | select(.snapshot_id == \"$snapshot\") | .latest")" = true ] || fail "no snapshot $snapshot: $body"

printf 'oops\n' >"$local_dir/a.txt"
rm "$local_dir/docs/report.bin"
printf 'stray\n' >"$remote_dir/stray.txt"
api POST "/profiles/$slug/backups/$target/restore" "{\"snapshot_id\": \"$snapshot\", \"restore_scope\": \"both\"}" 202
job=$(field .id)
wait_until 120 "restore job $job" backup_done "$job"
[ "$(field .status)" = completed ] || fail "the restore: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
# What the restore replaced or removed is kept aside, not deleted.
grep -rqx oops "$local_dir/.omnisync-trash/pre-restore" || fail "the replaced a.txt was not kept"
find "$remote_dir/.omnisync-trash/pre-restore" -name stray.txt | grep . >/dev/null || fail "the removed stray.txt was not kept"
# The next sync leaves the restored folders as they are.
sync_now "$slug" two_way
[ "$(field .status)" = completed ] || fail "the sync after the restore: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
pass "backup $snapshot verified; the restore brought both sides back"

# --- the audit trail ------------------------------------------------------------------------
step "audit trail"
audit_has '^profile\.create name="E2E Docs" outcome=ok '
audit_has "^sync\.start profile=$slug direction=two_way force=false outcome=ok "
audit_has "^conflict\.resolve conflict=$conflict resolution=keep_local outcome=ok "
audit_has "^sync\.start profile=$slug direction=two_way force=false status=409 code=sync_paused outcome=refused "
audit_has "^sync\.start profile=$slug direction=pull force=true outcome=ok "
audit_has "^backup\.target_create profile=$slug name=\"E2E backup\" type=local encrypted=false outcome=ok "
audit_has "^backup\.run profile=$slug target=$target outcome=ok "
audit_has "^backup\.restore profile=$slug target=$target snapshot=$snapshot scope=both outcome=ok "
pass "GET /logs?category=audit shows every action"
