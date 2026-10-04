#!/usr/bin/env bash
# End-to-end test of scripts/install.sh against images built from this
# checkout (CI job "installer" in .github/workflows/ci.yml). Not for users.
#
#   docker build -t ghcr.io/pan-fire/omnisync-backend:9999.0.0-ci .
#   docker build -t ghcr.io/pan-fire/omnisync-web:9999.0.0-ci frontend
#   scripts/test-installer-e2e.sh 9999.0.0-ci
#
# It stands up a test release (deploy/compose.yml, its SHA256SUMS and the
# version) for OMNISYNC_INSTALL_TEST_RELEASE_DIR, then, in a temporary
# folder and on non-default ports (API_PORT, WEB_PORT; default 18080 and
# 13080), runs the installer: install, status, a second run (up to date) and
# uninstall --purge --yes, checking the health endpoints, the API token and
# the web UI's /api path to the backend in between. Everything it created
# is removed at the end, also when a check fails.

set -euo pipefail

version=${1:?usage: $0 VERSION (the tag of the locally built images)}
root=$(cd "$(dirname "$0")/.." && pwd)
api_port=${API_PORT:-18080}
web_port=${WEB_PORT:-13080}
project=omnisync-e2e
work=$(mktemp -d "${TMPDIR:-/tmp}/omnisync-e2e.XXXXXX")
release="$work/release" dir="$work/install" sync="$work/sync"

fail () { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass () { printf 'ok: %s\n' "$*"; }

cleanup () {
  if [ -f "$dir/compose.yml" ]; then
    printf '\n--- backend log (last 40 lines) ---\n'
    docker compose --project-directory "$dir" -f "$dir/compose.yml" logs --tail 40 backend </dev/null || true
    docker compose --project-directory "$dir" -f "$dir/compose.yml" down --volumes </dev/null >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

for image in backend web; do
  docker image inspect "ghcr.io/pan-fire/omnisync-$image:$version" >/dev/null \
    || fail "build ghcr.io/pan-fire/omnisync-$image:$version first"
done

mkdir -p "$release"
cp "$root/deploy/compose.yml" "$release/compose.yml"
printf '%s\n' "$version" >"$release/VERSION"
(cd "$release" && sha256sum compose.yml >SHA256SUMS)
export OMNISYNC_INSTALL_TEST_RELEASE_DIR="$release"

installer () { bash "$root/scripts/install.sh" "$@" --dir "$dir" </dev/null; }

# --- install --------------------------------------------------------------
installer install --yes --sync-dir "$sync" --api-port "$api_port" --web-port "$web_port" \
  --project-name "$project" --no-ui-password --health-timeout 240
[ "$(stat -c %a "$dir/.env")" = 600 ] || fail ".env is not mode 0600"
grep -qx "OMNISYNC_VERSION=$version" "$dir/.env" || fail ".env does not name $version"
token=$(grep '^OMNISYNC_API_TOKEN=' "$dir/.env" | cut -d= -f2-)
[ "${#token}" -ge 32 ] || fail "no API token in .env"
pass "installed $version into $dir"

# --- health and the paths to the API -----------------------------------------
health=$(curl -fsS "http://127.0.0.1:$api_port/health") || fail "backend /health"
[[ $health == *'"status"'* ]] || fail "backend /health answered: $health"
[ "$(curl -fsS "http://127.0.0.1:$web_port/healthz")" = ok ] || fail "web UI /healthz"
pass "backend /health and web UI /healthz answer"

code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$api_port/profiles")
[ "$code" = 401 ] || fail "the API without a token answered $code, not 401"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "http://127.0.0.1:$api_port/profiles")
[ "$code" = 200 ] || fail "the API with the token answered $code, not 200"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$web_port/api/profiles")
[ "$code" = 200 ] || fail "the web UI's /api/profiles answered $code, not 200"
pass "API: 401 without the token, 200 with it; web UI -> API: /api/profiles 200"

# An audited action through the web UI: the audit trail names the client the
# web UI vouched for, and the web UI container as "via".
code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "http://127.0.0.1:$web_port/api/config" \
  -H "Origin: http://127.0.0.1:$web_port" -H 'Content-Type: application/json' -d '{"log_level": "INFO"}')
[ "$code" = 200 ] || fail "PUT /api/config through the web UI answered $code"
for _ in 1 2 3 4 5; do
  audit=$(docker compose --project-directory "$dir" -f "$dir/compose.yml" logs backend </dev/null 2>/dev/null \
    | grep 'backend.audit' | grep 'settings.update' || true)
  [ -z "$audit" ] || break
  sleep 1
done
[[ $audit == *" via="* ]] || fail "the audit line of the web UI's change has no via=: $audit"
printf '%s\n' "$audit"
pass "the audit trail names the browser's address and the web UI as via"

# --- status ------------------------------------------------------------------
status=$(installer status)
printf '%s\n' "$status"
grep -Eq "installed +$version" <<<"$status" || fail "status does not show $version"
grep -Eq "backend +healthy" <<<"$status" || fail "status: backend not healthy"
grep -Eq "web UI +healthy" <<<"$status" || fail "status: web UI not healthy"
pass "status"

# --- a second run: up to date --------------------------------------------------
again=$(installer --yes)
printf '%s\n' "$again"
grep -q "up to date" <<<"$again" || fail "the second run did not say up to date"
[ "$(curl -fsS "http://127.0.0.1:$web_port/healthz")" = ok ] || fail "web UI /healthz after the second run"
pass "second run: up to date, still healthy"

# --- uninstall --purge -----------------------------------------------------------
installer uninstall --purge --yes
if docker volume inspect "${project}_omnisync-data" >/dev/null 2>&1; then fail "the data volume is still there"; fi
[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ] || fail "containers are left"
[ ! -e "$dir/.env" ] && [ ! -e "$dir/compose.yml" ] || fail "compose.yml or .env is left"
[ -d "$sync" ] || fail "the sync folder was removed"
pass "uninstall --purge removed the containers, the volume, compose.yml and .env (the sync folder stays)"
