#!/usr/bin/env bash
# The update path end to end: the previous release, installed from GitHub
# and GHCR, updated by scripts/install.sh to the images built from this
# checkout (CI job "installer" in .github/workflows/ci.yml); then an update
# to a version that never becomes healthy, which must roll back. Not for
# users.
#
#   docker build -t ghcr.io/pan-fire/omnisync-backend:9999.0.0-ci .
#   docker build -t ghcr.io/pan-fire/omnisync-web:9999.0.0-ci frontend
#   scripts/test-upgrade-e2e.sh 9999.0.0-ci [FROM]
#
# FROM (default 0.12.0) is installed as a user would: its compose.yml from
# the GitHub release, checked against SHA256SUMS, its images pulled from
# ghcr.io. Then, through the API: a profile between two folders, a sync, a
# backup target and its backup, changed settings. `install.sh update` to
# VERSION (through OMNISYNC_INSTALL_TEST_RELEASE_DIR, as in
# scripts/test-installer-e2e.sh) must keep all of it, migrate the database
# (0011_drop_profile_backup_dir) and come up healthy. Last, an update to a
# broken build of VERSION (its backend exits at once) must roll back to
# VERSION with the data intact. On non-default ports (API_PORT, WEB_PORT;
# default 18280 and 13280). Everything it created is removed at the end
# (the FROM images stay), also when a check fails.

set -euo pipefail

version=${1:?usage: $0 VERSION [FROM] (VERSION: the tag of the locally built images)}
from=${2:-0.12.0}
root=$(cd "$(dirname "$0")/.." && pwd)
api_port=${API_PORT:-18280}
web_port=${WEB_PORT:-13280}
project=${PROJECT_NAME:-omnisync-upgrade-e2e}
work=$(mktemp -d "${TMPDIR:-/tmp}/omnisync-upgrade.XXXXXX")
dir="$work/install" sync="$work/sync" expected="$work/expected"
local_dir="$sync/docs" remote_dir="$sync/docs-remote" backups="$sync/backups"
api="http://127.0.0.1:$api_port"
token=""
# A version above VERSION whose backend never starts.
base=${version%%-*}
broken="${base%.*}.$((${base##*.} + 1))-broken"
# shellcheck source=scripts/e2e-common.sh
. "$root/scripts/e2e-common.sh"

cleanup () {
  teardown
  docker image rm "ghcr.io/pan-fire/omnisync-backend:$broken" "ghcr.io/pan-fire/omnisync-web:$broken" \
    >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

installer () { bash "$root/scripts/install.sh" "$@" --dir "$dir" </dev/null; }

health () {
  body=$(curl -fsS "$api/health") || fail "/health did not answer"
  [ "$(field .status) $(field .database_ok)" = "ok true" ] || fail "/health: $body"
  field .version
}

# What must survive an update, as the API shows it; the job history up to
# job $1 (a two-way profile syncs when its engine starts, so the start of
# the new version adds a job).
profile_state () {
  api GET "/profiles/$slug" 200
  field '{slug, name, local_dir, remote_dir, sync_mode, debounce_seconds, pull_interval_minutes,
          rclone_filter, rclone_args, max_retries, bwlimit, enabled, created_at}'
  api GET "/profiles/$slug/backups/$target" 200
  field '{name, target_type, target_path, backup_mode, retention_days, keep_last, frequency_hours,
          enabled, last_backup_status, last_verify_status}'
  api GET "/profiles/$slug/backups/$target/snapshots" 200
  field '[.[] | {snapshot_id, status}]'
  api GET "/profiles/$slug/jobs?limit=100" 200
  field "[.[] | select(.id <= $1) | {id, direction, status, files_changed}]"
  api GET /config 200
  field .
}

# --- the previous release, as a user installs it -----------------------------------
step "install $from from GitHub and ghcr.io"
unset OMNISYNC_INSTALL_TEST_RELEASE_DIR
installer install --version "$from" --yes --sync-dir "$sync" --api-port "$api_port" --web-port "$web_port" \
  --project-name "$project" --no-ui-password --health-timeout 240
read_token
[ "$(health)" = "$from" ] || fail "/health does not report $from"
[ "$(sql 'select version_num from alembic_version')" = 0010_backups ] || fail "$from's database is not at 0010_backups"
sql 'select name from pragma_table_info("sync_profiles")' | grep -x backup_dir >/dev/null || fail "$from's sync_profiles has no backup_dir"
pass "$from installed; its database is at 0010_backups"

# --- data, profiles and settings -------------------------------------------------------
step "data on $from"
add_local_remote e2elocal
mkdir -p "$expected/docs" "$local_dir" "$remote_dir" "$backups"
printf 'kept across the update\n' >"$expected/a.txt"
head -c 204800 /dev/urandom >"$expected/docs/data.bin"
cp -a "$expected/." "$local_dir/"
api POST /profiles "$(jq -n --arg l "$local_dir" --arg r "e2elocal:$remote_dir" \
  '{name: "Upgrade Docs", local_dir: $l, remote_dir: $r, debounce_seconds: 3600,
    pull_interval_minutes: 10080, rclone_args: ["--max-delete", "7", "--transfers", "2"],
    rclone_filter: ["- *.tmp"], max_retries: 2, bwlimit: "10M"}')" 201
slug=$(field .slug)
wait_idle "$slug"
sync_now "$slug" two_way
[ "$(field .status)" = completed ] || fail "the sync on $from: $body"
last_job=$(field .id)
same_tree "$expected" "$remote_dir"
api POST "/profiles/$slug/backups" "$(jq -n --arg p "$backups" \
  '{name: "Upgrade backup", target_type: "local", target_path: $p, keep_last: 5, frequency_hours: 48}')" 201
target=$(field .id)
backup_done () {
  call GET "/profiles/$slug/backups/$target/jobs/$1"
  [ "$status" = 200 ] && [ "$(field .status)" != running ]
}
api POST "/profiles/$slug/backups/$target/run" 202
job=$(field .id)
wait_until 120 "backup job $job" backup_done "$job"
[ "$(field .status)" = completed ] || fail "the backup on $from: $body"
api PUT /config '{"log_level": "WARNING", "history_days": 42}' 200
before=$(profile_state "$last_job")
env_before=$(grep -v '^OMNISYNC_VERSION=' "$dir/.env")
pass "profile $slug synced, backup target $target with a backup, settings changed"

# --- update to this checkout's images ------------------------------------------------------
step "update $from -> $version"
test_release "$work/release" "$version"
export OMNISYNC_INSTALL_TEST_RELEASE_DIR="$work/release"
out=$(installer update --yes --health-timeout 240) || fail "the update failed: $out"
printf '%s\n' "$out"
grep -q "updated to $version" <<<"$out" || fail "the update did not say it updated"
grep -qx "OMNISYNC_VERSION=$version" "$dir/.env" || fail ".env does not name $version"
[ "$(grep -v '^OMNISYNC_VERSION=' "$dir/.env")" = "$env_before" ] || fail "the update changed other .env settings"
snapshot=$(find "$dir/backups" -name "omnisync-data-$from-*.tar.gz")
[ -n "$snapshot" ] || fail "no backup of $from's data volume"
tar tzf "$snapshot" | grep -x './omnisync.db' >/dev/null || fail "the backup $snapshot holds no database"
[ "$(health)" = "$(cat "$root/VERSION")" ] || fail "/health does not report this checkout's version"
[ "$(sql 'select version_num from alembic_version')" = 0011 ] || fail "the database was not migrated to 0011"
if sql 'select name from pragma_table_info("sync_profiles")' | grep -x backup_dir >/dev/null; then
  fail "0011_drop_profile_backup_dir did not drop sync_profiles.backup_dir"
fi
pass "updated; healthy; migrated to 0011 (backup_dir dropped); the volume was backed up first"

after=$(profile_state "$last_job")
diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") >&2 || fail "the profile, backups, jobs or settings changed"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
# The updated version carries on where the old one stopped: no resync.
api GET "/profiles/$slug/sync/status" 200
[ "$(field .resync_required)" = false ] || fail "the update lost the two-way state: $body"
printf 'written after the update\n' >"$expected/after.txt"
cp "$expected/after.txt" "$local_dir/"
wait_idle "$slug"
sync_now "$slug" two_way
[ "$(field '"\(.direction) \(.status)"')" = "two_way completed" ] || fail "the sync after the update: $body"
same_tree "$expected" "$remote_dir"
api POST "/profiles/$slug/backups/$target/run" 202
job=$(field .id)
wait_until 120 "backup job $job" backup_done "$job"
[ "$(field .status)" = completed ] || fail "a backup after the update: $body"
pass "profiles, backups, job history and settings survived; syncing and backups go on"

# --- an update that never becomes healthy rolls back ----------------------------------------
step "update $version -> $broken (unhealthy): rollback"
printf 'FROM ghcr.io/pan-fire/omnisync-backend:%s\nENTRYPOINT ["false"]\n' "$version" \
  | docker build -q -t "ghcr.io/pan-fire/omnisync-backend:$broken" - >/dev/null
docker tag "ghcr.io/pan-fire/omnisync-web:$version" "ghcr.io/pan-fire/omnisync-web:$broken"
test_release "$work/release" "$broken"
wait_idle "$slug"
api GET "/profiles/$slug/jobs?limit=1" 200
last_job=$(field '.[0].id')
before=$(profile_state "$last_job")
env_before=$(cat "$dir/.env")
compose_before=$(cat "$dir/compose.yml")
# The timeout holds for both waits: the broken version's and the rollback's.
if out=$(installer update --yes --health-timeout 60 2>&1); then fail "the update to $broken succeeded: $out"; fi
printf '%s\n' "$out"
grep -q "rolling back to $version" <<<"$out" || fail "the failed update did not roll back"
grep -q "$version runs again" <<<"$out" || fail "the rollback did not bring $version back"
[ "$(cat "$dir/.env")" = "$env_before" ] || fail ".env was not restored"
[ "$(cat "$dir/compose.yml")" = "$compose_before" ] || fail "compose.yml was not restored"
[ ! -e "$dir/compose.yml.previous" ] || fail "compose.yml.previous is left"
[ "$(health)" = "$(cat "$root/VERSION")" ] || fail "/health after the rollback"
[ "$(dc ps --format '{{.Image}}' | sort -u | tr '\n' ' ')" \
  = "ghcr.io/pan-fire/omnisync-backend:$version ghcr.io/pan-fire/omnisync-web:$version " ] \
  || fail "the containers do not run $version again"
[ "$(find "$dir/backups" -name "omnisync-data-$version-*.tar.gz" | wc -l)" = 1 ] || fail "no backup before the failed update"
after=$(profile_state "$last_job")
diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") >&2 || fail "the failed update changed the data"
wait_idle "$slug"
sync_now "$slug" two_way
[ "$(field .status)" = completed ] || fail "the sync after the rollback: $body"
same_tree "$expected" "$local_dir"
same_tree "$expected" "$remote_dir"
pass "the unhealthy $broken was rolled back to $version; data and syncing intact"
