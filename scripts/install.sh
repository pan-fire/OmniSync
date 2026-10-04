#!/usr/bin/env bash
# OmniSync installer: installs, updates, checks and removes OmniSync's
# released images (backend and web UI) with docker compose.
#
#   curl -fsSL https://raw.githubusercontent.com/pan-fire/OmniSync/main/scripts/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --yes --sync-dir /srv/sync
#   bash install.sh [command] [options]          (bash install.sh --help)
#
# What it downloads: the GitHub API's "latest release" answer and the
# release's assets from github.com/pan-fire/OmniSync (compose.yml,
# SHA256SUMS, its signature and, with --osync, the osync binary); the images
# come from ghcr.io through `docker compose pull`. compose.yml is checked
# against SHA256SUMS before it is used, and SHA256SUMS against its Sigstore
# signature when cosign is installed. Releases before 0.12.0 attach no
# compose.yml; for them the copy built into this script (below) is used.
#
# Nothing downloaded is executed or evaluated, .env is never sourced, and
# the API token and the web UI password are never printed. Prompts read
# from /dev/tty, so they work when this script is piped into bash; without
# a terminal, --yes takes the defaults.
#
# Tests: backend/tests/test_install_script.py (docker and curl are stubbed),
# and CI's "installer" job against real images (OMNISYNC_INSTALL_TEST_RELEASE_DIR,
# see "CI test release" below; not for installs).

set -euo pipefail

readonly REPO="pan-fire/OmniSync"
readonly RELEASES_URL="https://github.com/$REPO/releases"
readonly API_URL="https://api.github.com/repos/$REPO"
readonly IMAGE_PREFIX="ghcr.io/pan-fire/omnisync-"
readonly DOCS_URL="https://github.com/$REPO/blob/main/docs/gem/getting-started.md"
readonly CERT_ISSUER="https://token.actions.githubusercontent.com"
# The oldest release this script installs, and the first that attaches
# compose.yml to its GitHub release (older ones use embedded_compose).
readonly MIN_VERSION="0.11.0"
readonly FIRST_ASSET_VERSION="0.12.0"

COMMAND=""
DIR=""
DIR_SET=0
VERSION=""
SYNC_DIR=""
BIND_ADDRESS=""
API_PORT=""
WEB_PORT=""
PROJECT=""
UI_PASSWORD="ask"   # ask | yes | no
OSYNC=0
NO_BACKUP=0
PURGE=0
YES=0
DRY_RUN=0
HEALTH_TIMEOUT=180
SETTINGS_GIVEN=""   # install-only flags given, for the warning on an existing install

TMP=""
OS=""
ARCH=""
DOWNLOADER=""

# ---------------------------------------------------------------- output

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\033[1m' DIM=$'\033[2m' RED=$'\033[31m' YELLOW=$'\033[33m' GREEN=$'\033[32m' RESET=$'\033[0m'
else
  BOLD="" DIM="" RED="" YELLOW="" GREEN="" RESET=""
fi

step () { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
info () { printf '    %s\n' "$*"; }
ok () { printf '    %s%s%s\n' "$GREEN" "$*" "$RESET"; }
warn () { printf '%swarning:%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die () { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
usage_error () { printf '%serror:%s %s\nRun with --help for the usage.\n' "$RED" "$RESET" "$*" >&2; exit 2; }

usage () {
  cat <<EOF
OmniSync installer: install, update, check or remove OmniSync (the released
backend and web UI images, run with docker compose).

Usage:
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/scripts/install.sh | bash -s -- [command] [options]
  bash install.sh [command] [options]

Commands:
  (none)      install, or on an existing install: update it if a newer
              release exists (after a confirmation), else start it if stopped
  install     install into --dir (refuses to touch an existing install)
  update      update an existing install to the latest release (or --version)
  status      installed and latest version, container health, URLs, folders
  uninstall   stop and remove the containers; keeps the data volume and .env
  help        this text

Install options (a fresh install only; afterwards edit .env):
  --dir DIR              install folder (default ~/omnisync; /opt/omnisync as root)
  --version X.Y.Z        release to install or update to (default: the latest)
  --sync-dir DIR         the host folder OmniSync may sync (default ~/OmniSync)
  --bind-address ADDR    host address of the API port (default 127.0.0.1)
  --api-port PORT        host port of the API (default 8000)
  --web-port PORT        host port of the web UI (default 3000)
  --project-name NAME    compose project name (default omnisync); names the
                         data volume NAME_omnisync-data
  --ui-password-prompt   set a web UI password (asked without echo; only its
                         hash is stored)
  --no-ui-password       do not ask for a web UI password

Other options:
  --osync                also install the osync terminal client to ~/.local/bin
  --no-backup            update without backing up the data volume first
  --purge                uninstall: also delete the data volume, compose.yml
                         and .env (asks you to type the volume name)
  --health-timeout SECS  how long to wait for the health checks (default 180)
  -y, --yes              do not ask; take the defaults (needed without a terminal)
  -n, --dry-run          print what would be done, change nothing
  -h, --help             this text

Files: DIR/compose.yml (from the release), DIR/.env (settings, mode 0600),
DIR/backups/ (data volume backups taken before updates).
Docs: $DOCS_URL
EOF
}

# ---------------------------------------------------------------- arguments

need_value () {
  [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "$1 needs a value"
}

parse_args () {
  while [ "$#" -gt 0 ]; do
    case $1 in
      --*=*) set -- "${1%%=*}" "${1#*=}" "${@:2}"; continue ;;
    esac
    case $1 in
      install|update|status|uninstall|help)
        [ -z "$COMMAND" ] || usage_error "only one command, got '$COMMAND' and '$1'"
        COMMAND=$1 ;;
      --dir) need_value "$@"; DIR=$2; DIR_SET=1; shift ;;
      --version) need_value "$@"; VERSION=${2#v}; shift ;;
      --sync-dir) need_value "$@"; SYNC_DIR=$2; SETTINGS_GIVEN="$SETTINGS_GIVEN $1"; shift ;;
      --bind-address) need_value "$@"; BIND_ADDRESS=$2; SETTINGS_GIVEN="$SETTINGS_GIVEN $1"; shift ;;
      --api-port) need_value "$@"; API_PORT=$2; SETTINGS_GIVEN="$SETTINGS_GIVEN $1"; shift ;;
      --web-port) need_value "$@"; WEB_PORT=$2; SETTINGS_GIVEN="$SETTINGS_GIVEN $1"; shift ;;
      --project-name) need_value "$@"; PROJECT=$2; SETTINGS_GIVEN="$SETTINGS_GIVEN $1"; shift ;;
      --ui-password-prompt) UI_PASSWORD=yes ;;
      --no-ui-password) UI_PASSWORD=no ;;
      --osync) OSYNC=1 ;;
      --no-backup) NO_BACKUP=1 ;;
      --purge) PURGE=1 ;;
      --health-timeout) need_value "$@"; HEALTH_TIMEOUT=$2; shift ;;
      -y|--yes) YES=1 ;;
      -n|--dry-run) DRY_RUN=1 ;;
      -h|--help) COMMAND=help ;;
      *) usage_error "unknown argument: $1" ;;
    esac
    shift
  done

  if [ -n "$VERSION" ]; then
    is_version "$VERSION" || usage_error "--version: '$VERSION' is not a version like 0.12.0"
  fi
  if [ -n "$API_PORT" ]; then is_port "$API_PORT" || usage_error "--api-port: '$API_PORT' is not a port"; fi
  if [ -n "$WEB_PORT" ]; then is_port "$WEB_PORT" || usage_error "--web-port: '$WEB_PORT' is not a port"; fi
  if [ -n "$API_PORT" ] && [ "$API_PORT" = "$WEB_PORT" ]; then usage_error "--api-port and --web-port must differ"; fi
  if [ -n "$BIND_ADDRESS" ] && ! [[ $BIND_ADDRESS =~ ^[0-9A-Fa-f.:]+$ ]]; then
    usage_error "--bind-address: '$BIND_ADDRESS' is not an IP address (e.g. 127.0.0.1 or 0.0.0.0)"
  fi
  if [ -n "$PROJECT" ] && ! [[ $PROJECT =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    usage_error "--project-name: use lowercase letters, digits, '-' and '_'"
  fi
  [[ $HEALTH_TIMEOUT =~ ^[0-9]+$ ]] || usage_error "--health-timeout: '$HEALTH_TIMEOUT' is not a number of seconds"
  if [ "$PURGE" = 1 ] && [ "$COMMAND" != uninstall ]; then usage_error "--purge only goes with uninstall"; fi
  return 0
}

is_version () { [[ $1 =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$ ]]; }
is_port () { [[ $1 =~ ^[1-9][0-9]{0,4}$ ]] && [ "$1" -le 65535 ]; }

# version_lt A B: A is older than B (X.Y.Z, a pre-release before its release).
version_lt () {
  local a=${1%%-*} b=${2%%-*} i x y
  local IFS=.
  # shellcheck disable=SC2206  # split on dots on purpose
  local av=($a) bv=($b)
  for i in 0 1 2; do
    x=${av[$i]:-0} y=${bv[$i]:-0}
    if [ "$x" -lt "$y" ]; then return 0; fi
    if [ "$x" -gt "$y" ]; then return 1; fi
  done
  # Same X.Y.Z: a pre-release is older than the release.
  [[ $1 == *-* && $2 != *-* ]] && return 0
  [[ $1 == *-* && $2 == *-* && $1 < $2 ]]
}

# ---------------------------------------------------------------- prompts

have_tty () { { : </dev/tty; } 2>/dev/null; }

# ask PROMPT DEFAULT: the answer (or the default with --yes / --dry-run).
ask () {
  local answer
  if [ "$YES" = 1 ] || [ "$DRY_RUN" = 1 ]; then printf '%s' "$2"; return 0; fi
  have_tty || die "no terminal to ask '$1'; rerun with --yes to take the defaults (or pass the setting as a flag)"
  printf '%s [%s]: ' "$1" "$2" >/dev/tty
  IFS= read -r answer </dev/tty || answer=""
  printf '%s' "${answer:-$2}"
}

# confirm PROMPT y|n: yes or no; --yes says yes.
confirm () {
  local answer hint="[Y/n]"
  [ "$2" = n ] && hint="[y/N]"
  if [ "$YES" = 1 ]; then return 0; fi
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  have_tty || die "no terminal to confirm '$1'; rerun with --yes"
  printf '%s %s ' "$1" "$hint" >/dev/tty
  IFS= read -r answer </dev/tty || answer=""
  case ${answer:-$2} in
    y|Y|yes|Yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------- helpers

# run CMD...: runs it, or prints it with --dry-run.
run () {
  if [ "$DRY_RUN" = 1 ]; then
    printf '    %s[dry-run]%s %s\n' "$DIM" "$RESET" "$*"
    return 0
  fi
  "$@"
}

cleanup () { if [ -n "$TMP" ]; then rm -rf "$TMP"; fi; }

make_tmp () {
  if [ -z "$TMP" ]; then
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/omnisync-install.XXXXXX")
    trap cleanup EXIT
  fi
}

# download URL OUT: only release assets of this repository and its API.
download () {
  local url=$1 out=$2
  case $url in
    "$RELEASES_URL"/download/*|"$API_URL"/releases/*) ;;
    *) die "refusing to download from an unexpected address: $url" ;;
  esac
  if test_release_active; then test_release_download "$url" "$out"; return; fi
  if [ "$DOWNLOADER" = curl ]; then
    curl -fsSL --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 20 -o "$out" "$url" </dev/null
  else
    wget -q --https-only -O "$out" "$url" </dev/null
  fi
}

sha256_of () {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# random_hex BYTES: hex from openssl, or /dev/urandom without it.
random_hex () {
  local hex=""
  if command -v openssl >/dev/null 2>&1; then
    hex=$(openssl rand -hex "$1" 2>/dev/null) || hex=""
  fi
  if ! [[ $hex =~ ^[0-9a-f]+$ ]] || [ "${#hex}" -ne $(($1 * 2)) ]; then
    hex=$(od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n')
  fi
  [ "${#hex}" -eq $(($1 * 2)) ] || die "could not generate random bytes (no openssl and no /dev/urandom)"
  printf '%s' "$hex"
}

# env_get KEY FILE: the value of KEY in a .env file (never sourced).
env_get () {
  [ -f "$2" ] || return 0
  grep -E "^$1=" "$2" | tail -n 1 | cut -d= -f2- || true
}

# env_set KEY VALUE FILE: replaces KEY's line (or appends it), keeping the
# rest of the file and its 0600 mode.
env_set () {
  local key=$1 value=$2 file=$3 tmp
  if [ "$DRY_RUN" = 1 ]; then
    case $key in
      *TOKEN*|*PASSWORD*|*SECRET*) value="<hidden>" ;;
    esac
    printf '    %s[dry-run]%s set %s=%s in %s\n' "$DIM" "$RESET" "$key" "$value" "$file"
    return 0
  fi
  tmp="$file.tmp.$$"
  (
    umask 077
    if grep -qE "^$key=" "$file"; then
      KEY=$key VALUE=$value awk 'BEGIN { k = ENVIRON["KEY"] "=" } index($0, k) == 1 { print k ENVIRON["VALUE"]; next } { print }' "$file" >"$tmp"
    else
      cat "$file" >"$tmp"
      printf '%s=%s\n' "$key" "$value" >>"$tmp"
    fi
  )
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

compose () {
  docker compose --project-directory "$DIR" -f "$DIR/compose.yml" "$@" </dev/null
}

# ---------------------------------------------------------------- preflight

preflight () {
  local need_ports=$1 problems=0 out kernel

  case $(uname -s) in
    Linux)
      OS=linux
      kernel=$(uname -r)
      if [[ $kernel == *[Mm]icrosoft* ]] || [ -n "${WSL_DISTRO_NAME:-}" ]; then
        info "WSL detected: use Docker Desktop with its WSL integration on for this distribution."
      fi ;;
    Darwin) OS=darwin ;;
    *) die "unsupported system $(uname -s): OmniSync's installer runs on Linux, macOS and Windows (WSL 2 with Docker Desktop)" ;;
  esac

  case $(uname -m) in
    x86_64|amd64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) die "unsupported architecture $(uname -m): the images are built for amd64 (x86-64) and arm64 only; 32-bit ARM (armv7) is not supported" ;;
  esac

  if command -v curl >/dev/null 2>&1; then
    DOWNLOADER=curl
  elif command -v wget >/dev/null 2>&1; then
    DOWNLOADER=wget
  else
    warn "neither curl nor wget is installed. Install one (e.g. sudo apt install curl)."
    problems=$((problems + 1))
  fi

  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    warn "no sha256sum or shasum to check downloads. Install coreutils (Linux) or Perl's shasum (macOS)."
    problems=$((problems + 1))
  fi

  if ! command -v openssl >/dev/null 2>&1 && ! [ -r /dev/urandom ]; then
    warn "no openssl and no /dev/urandom for the API token. Install openssl."
    problems=$((problems + 1))
  fi

  if ! command -v docker >/dev/null 2>&1; then
    warn "docker is not installed. Install Docker Engine (https://docs.docker.com/engine/install/) or Docker Desktop (macOS, WSL)."
    problems=$((problems + 1))
  elif ! out=$(docker info --format '{{.ServerVersion}}' 2>&1 </dev/null); then
    if [[ $out == *"permission denied"* ]]; then
      warn "you may not use Docker: your user is not in the docker group. Fix it with
    sudo usermod -aG docker \"\$USER\"
  then log out and back in (or run 'newgrp docker') and rerun this installer.
  (This installer never runs sudo itself; running it with sudo works too.)"
    else
      warn "the Docker daemon is not reachable. Start it (sudo systemctl start docker, or open Docker Desktop) and rerun.
  docker said: $(printf '%s' "$out" | head -n 1)"
    fi
    problems=$((problems + 1))
  elif ! out=$(docker compose version --short 2>/dev/null </dev/null) || ! [[ ${out#v} =~ ^[2-9]\. ]]; then
    warn "the docker compose v2 plugin is missing ('docker compose', not 'docker-compose'). Install the docker-compose-plugin package (https://docs.docker.com/compose/install/linux/)."
    problems=$((problems + 1))
  fi

  if [ "$need_ports" = 1 ]; then
    local port
    for port in "$API_PORT" "$WEB_PORT"; do
      if port_in_use "$port"; then
        warn "port $port is in use. Free it, or pick another with --api-port/--web-port."
        problems=$((problems + 1))
      fi
    done
  fi

  [ "$problems" -eq 0 ] || die "$problems preflight check(s) failed; see above."
}

port_in_use () {
  if command -v ss >/dev/null 2>&1; then
    [ -n "$(ss -Htln "sport = :$1" 2>/dev/null)" ] && return 0
    return 1
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# ---------------------------------------------------------------- releases

latest_version () {
  local file tag
  make_tmp
  file="$TMP/latest.json"
  download "$API_URL/releases/latest" "$file" \
    || die "could not ask GitHub for the latest release ($API_URL/releases/latest). Check the network, or pass --version X.Y.Z."
  tag=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p' "$file" | head -n 1)
  is_version "$tag" || die "GitHub's latest release has an unexpected tag: '$tag'"
  printf '%s' "$tag"
}

# verify_sums VERSION: downloads SHA256SUMS of a release into $TMP/VERSION/
# and checks its signature when cosign is installed.
fetch_sums () {
  local v=$1 d="$TMP/$1"
  [ -f "$d/SHA256SUMS" ] && return 0
  mkdir -p "$d"
  download "$RELEASES_URL/download/v$v/SHA256SUMS" "$d/SHA256SUMS" \
    || die "could not download SHA256SUMS of release $v ($RELEASES_URL/tag/v$v). Does the release exist?"
  if command -v cosign >/dev/null 2>&1; then
    download "$RELEASES_URL/download/v$v/SHA256SUMS.sigstore.json" "$d/SHA256SUMS.sigstore.json" \
      || die "could not download the signature of SHA256SUMS for $v"
    cosign verify-blob --bundle "$d/SHA256SUMS.sigstore.json" \
      --certificate-identity "https://github.com/$REPO/.github/workflows/release.yml@refs/tags/v$v" \
      --certificate-oidc-issuer "$CERT_ISSUER" "$d/SHA256SUMS" >/dev/null 2>&1 </dev/null \
      || die "the signature of SHA256SUMS for $v does not verify: refusing to continue"
    ok "SHA256SUMS signature verified (cosign)"
  else
    info "cosign not installed: checking SHA256 checksums only (see the README's \"Verify a release\")"
  fi
}

# check_sum VERSION NAME FILE: FILE's SHA256 matches NAME's line in SHA256SUMS.
check_sum () {
  local expected actual
  expected=$(awk -v n="$2" '$2 == n || $2 == "*" n { print $1; exit }' "$TMP/$1/SHA256SUMS")
  [ -n "$expected" ] || die "SHA256SUMS of release $1 does not list $2"
  actual=$(sha256_of "$3")
  [ "$expected" = "$actual" ] || die "checksum mismatch for $2 of release $1 (expected $expected, got $actual): refusing to use it"
}

# fetch_compose VERSION OUT: the release's compose.yml, verified.
fetch_compose () {
  local v=$1 out=$2
  make_tmp
  fetch_sums "$v"
  if grep -qE '[[:space:]]\*?compose\.yml$' "$TMP/$v/SHA256SUMS"; then
    download "$RELEASES_URL/download/v$v/compose.yml" "$out" || die "could not download compose.yml of release $v"
    check_sum "$v" compose.yml "$out"
    ok "compose.yml of $v matches SHA256SUMS"
  elif version_lt "$v" "$FIRST_ASSET_VERSION"; then
    # Releases before 0.12.0 attach no compose.yml; their images run with
    # the same file, which this script carries.
    embedded_compose >"$out"
    info "release $v attaches no compose.yml: using the copy built into this installer"
  else
    die "release $v has no compose.yml in its SHA256SUMS: refusing to continue"
  fi
}

# ---------------------------------------------------------------- CI test release
#
# For the installer's end-to-end test in CI only (.github/workflows/ci.yml,
# job "installer"), never for a real install. OMNISYNC_INSTALL_TEST_RELEASE_DIR
# names a folder that stands in for a GitHub release:
#   VERSION      the version the "latest release" answer names
#   compose.yml  the release's compose file
#   SHA256SUMS   its checksum, checked as for a real release
# With it set, download() serves these files instead of fetching anything,
# and no image is pulled: CI builds both images from the pull request's code
# and tags them ghcr.io/pan-fire/omnisync-{backend,web}:VERSION locally.
# Everything else (checksum, .env, ports, health checks) runs as usual.

test_release_active () { [ -n "${OMNISYNC_INSTALL_TEST_RELEASE_DIR:-}" ]; }

# test_release_check: the folder holds what it must; says it is a test.
test_release_check () {
  local dir=${OMNISYNC_INSTALL_TEST_RELEASE_DIR:-} name
  test_release_active || return 0
  for name in VERSION compose.yml SHA256SUMS; do
    [ -f "$dir/$name" ] || die "OMNISYNC_INSTALL_TEST_RELEASE_DIR ($dir) has no $name"
  done
  warn "OMNISYNC_INSTALL_TEST_RELEASE_DIR is set: using the test release in $dir and the local images (for CI tests only)"
}

# test_release_download URL OUT: what download() serves for URL in the test.
test_release_download () {
  local url=$1 out=$2 dir=$OMNISYNC_INSTALL_TEST_RELEASE_DIR version rest
  version=$(tr -d '[:space:]' <"$dir/VERSION")
  case $url in
    "$API_URL"/releases/latest)
      printf '{"tag_name": "v%s"}\n' "$version" >"$out" ;;
    "$RELEASES_URL/download/v$version"/*)
      rest=${url#"$RELEASES_URL/download/v$version/"}
      case $rest in */*|"") return 1 ;; esac
      [ -f "$dir/$rest" ] || return 1
      cp "$dir/$rest" "$out" ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------- installs

default_dir () {
  if [ "$(id -u)" = 0 ]; then printf '/opt/omnisync'; else printf '%s/omnisync' "$HOME"; fi
}

# The home of the user who ran the script (through sudo, too).
user_home () {
  local home=""
  if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] && command -v getent >/dev/null 2>&1; then
    home=$(getent passwd "$SUDO_USER" | cut -d: -f6) || home=""
  fi
  printf '%s' "${home:-$HOME}"
}

is_install_dir () { [ -f "$1/compose.yml" ] && [ -f "$1/.env" ] && grep -q '^OMNISYNC_VERSION=' "$1/.env"; }

# OmniSync containers: project|working dir|image|state|mounts, one per line.
omnisync_containers () {
  docker ps -a --format '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.Image}}|{{.State}}|{{.Mounts}}' </dev/null 2>/dev/null \
    | grep -E "\|${IMAGE_PREFIX//./\\.}|omnisync-data" || true
}

# Picks the install to work on: --dir, else the default folder, else the
# folder of running installer containers. Sets DIR; true if one exists.
find_install () {
  local wd image
  [ -n "$DIR" ] || DIR=$(default_dir)
  if is_install_dir "$DIR"; then return 0; fi
  [ "$DIR_SET" = 1 ] && return 1
  while IFS='|' read -r _ wd image _ _; do
    if [[ $image == "$IMAGE_PREFIX"* ]] && [ -n "$wd" ] && is_install_dir "$wd"; then
      DIR=$wd
      info "found an existing install in $DIR"
      return 0
    fi
  done <<EOF
$(omnisync_containers)
EOF
  return 1
}

# Other OmniSync instances: refuse to start a second one next to running
# containers, and explain how to move a source-built install over.
check_other_installs () {
  local project wd image state running_other=0 found_source=0
  while IFS='|' read -r project wd image state _; do
    [ -n "$project$image" ] || continue
    [ "$wd" = "$DIR" ] && continue
    if [[ $image == "$IMAGE_PREFIX"* ]]; then
      if [ "$state" = running ]; then
        warn "OmniSync already runs from $wd (compose project $project). Manage that install with --dir \"$wd\"."
        running_other=1
      fi
    elif [ "$found_source" = 0 ] && [ "$project" = "$PROJECT" ] && [ "$state" != running ]; then
      found_source=1
      info "a stopped OmniSync built from source in ${wd:-an unknown folder} uses project $project: this install takes over its containers and reuses its data volume ${project}_omnisync-data. Use the same sync folder as its .env (OMNISYNC_SYNC_DIR) so the profiles' paths still work."
    elif [ "$found_source" = 0 ]; then
      found_source=1
      warn "OmniSync built from source was found in ${wd:-an unknown folder} (compose project $project, image $image, state $state).
  To switch to the released images without losing data:
    1. stop it, keeping its volume:   cd \"$wd\" && docker compose down     (never 'down -v')
    2. rerun this installer with      --project-name $project --sync-dir <its OMNISYNC_SYNC_DIR>
       so it reuses the volume ${project}_omnisync-data and the same folder paths.
  The installer writes a new API token; give it to osync (OMNISYNC_API_KEY)."
      [ "$state" = running ] && running_other=1
    fi
  done <<EOF
$(omnisync_containers)
EOF
  [ "$running_other" = 0 ] || die "stop the other OmniSync first (see above); two would fight over the ports and the data."

  # A data volume of another project (e.g. a stopped source install).
  local vol vproject
  while IFS='|' read -r vol vproject; do
    [ -n "$vol" ] || continue
    [ "$vol" = "${PROJECT}_omnisync-data" ] && continue
    [ "$found_source" = 1 ] && continue
    warn "an OmniSync data volume $vol exists (project ${vproject:-unknown}). This install gets its own, empty volume ${PROJECT}_omnisync-data; to keep using $vol instead, rerun with --project-name ${vproject:-${vol%_omnisync-data}}."
    if ! confirm "Install with a new, empty data volume?" y; then die "stopped; nothing was changed"; fi
  done <<EOF
$(docker volume ls --format '{{.Name}}|{{.Label "com.docker.compose.project"}}' </dev/null 2>/dev/null | grep -E '_omnisync-data\|' || true)
EOF
}

load_install () {
  INSTALLED=$(env_get OMNISYNC_VERSION "$DIR/.env")
  is_version "$INSTALLED" || die "$DIR/.env has no valid OMNISYNC_VERSION ('$INSTALLED')"
  PROJECT=$(env_get COMPOSE_PROJECT_NAME "$DIR/.env")
  if [ -z "$PROJECT" ]; then
    PROJECT=$(basename "$DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
  fi
  API_PORT=$(env_get OMNISYNC_API_PORT "$DIR/.env"); API_PORT=${API_PORT:-8000}
  WEB_PORT=$(env_get OMNISYNC_WEB_PORT "$DIR/.env"); WEB_PORT=${WEB_PORT:-3000}
  BIND_ADDRESS=$(env_get OMNISYNC_BIND_ADDRESS "$DIR/.env"); BIND_ADDRESS=${BIND_ADDRESS:-127.0.0.1}
  SYNC_DIR=$(env_get OMNISYNC_SYNC_DIR "$DIR/.env")
  if [ -n "$SETTINGS_GIVEN" ]; then
    warn "ignored for an existing install:$SETTINGS_GIVEN (edit $DIR/.env and run 'docker compose up -d' there to change them)"
  fi
}

# container_state SERVICE: healthy, starting, unhealthy, running, exited, ... or missing.
container_state () {
  local id state
  id=$(compose ps -a -q "$1" 2>/dev/null | head -n 1) || id=""
  [ -n "$id" ] || { printf 'missing'; return 0; }
  state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" </dev/null 2>/dev/null) || state=unknown
  printf '%s' "${state:-unknown}"
}

wait_healthy () {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) backend frontend
  if [ "$DRY_RUN" = 1 ]; then info "[dry-run] would wait up to ${HEALTH_TIMEOUT}s for both health checks"; return 0; fi
  step "Waiting for the health checks (up to ${HEALTH_TIMEOUT}s)"
  while :; do
    backend=$(container_state backend)
    frontend=$(container_state frontend)
    if [ "$backend" = healthy ] && [ "$frontend" = healthy ]; then
      ok "backend (/health) and web UI (/healthz) are healthy"
      return 0
    fi
    case "$backend $frontend" in
      *exited*|*dead*|*missing*) warn "a container is not running: backend $backend, web UI $frontend"; return 1 ;;
    esac
    if [ "$SECONDS" -ge "$deadline" ]; then
      warn "not healthy after ${HEALTH_TIMEOUT}s: backend $backend, web UI $frontend"
      return 1
    fi
    sleep 2
  done
}

ui_url () { printf 'http://127.0.0.1:%s' "$WEB_PORT"; }
api_url () {
  local host=$BIND_ADDRESS
  case $host in 0.0.0.0|::|"") host=127.0.0.1 ;; esac
  [[ $host == *:* ]] && host="[$host]"
  printf 'http://%s:%s' "$host" "$API_PORT"
}

# ---------------------------------------------------------------- install

detect_timezone () {
  local tz=${TZ:-}
  if [ -z "$tz" ] && [ -r /etc/timezone ]; then tz=$(head -n 1 /etc/timezone); fi
  if [ -z "$tz" ] && command -v timedatectl >/dev/null 2>&1; then
    tz=$(timedatectl show -p Timezone --value 2>/dev/null </dev/null) || tz=""
  fi
  if [ -z "$tz" ] && [ -L /etc/localtime ]; then
    tz=$(readlink /etc/localtime | sed -n 's|.*/zoneinfo/||p')
  fi
  tz=${tz#:}
  [[ $tz =~ ^[A-Za-z0-9_+/-]+$ ]] || tz=UTC
  printf '%s' "$tz"
}

# The ids files are written as: the invoking user's, also through sudo.
detect_ids () {
  PUID=$(id -u) PGID=$(id -g)
  if [ "$PUID" = 0 ]; then
    if [ -n "${SUDO_UID:-}" ] && [ "${SUDO_UID}" != 0 ]; then
      PUID=$SUDO_UID PGID=${SUDO_GID:-$SUDO_UID}
    else
      PUID=1000 PGID=1000
      warn "running as root: OmniSync will write files as 1000:1000 (never root). Change PUID/PGID in .env if that is not your user."
    fi
  fi
}

check_path_value () {
  # .env values are not quoted: refuse what compose would read differently.
  case $2 in
    /*) ;;
    *) die "$1 must be an absolute path: $2" ;;
  esac
  local bad='[$"\#'"'"'[:cntrl:]]'
  if [[ $2 =~ $bad ]]; then die "$1 may not contain \$, quotes, backslashes, # or control characters: $2"; fi
}

hash_ui_password () {
  local pw again hash
  have_tty || die "--ui-password-prompt needs a terminal to read the password"
  printf 'Web UI password (12+ characters, not shown): ' >/dev/tty
  IFS= read -rs pw </dev/tty || pw=""
  printf '\nRepeat it: ' >/dev/tty
  IFS= read -rs again </dev/tty || again=""
  printf '\n' >/dev/tty
  [ "$pw" = "$again" ] || die "the passwords do not match; nothing was stored. Set one later as the README's \"Built-in login\" shows."
  [ "${#pw}" -ge 12 ] || die "use at least 12 characters (a few random words work well); nothing was stored."
  # The web image's own script hashes it (scrypt); the password goes in
  # through a pipe, never on a command line.
  hash=$(printf '%s\n' "$pw" | docker run --rm -i --network none "${IMAGE_PREFIX}web:$VERSION" node scripts/hash-password.mjs) \
    || die "hashing the password failed; nothing was stored"
  pw="" again=""
  [[ $hash =~ ^scrypt:[0-9]+:[0-9]+:[0-9]+:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$ ]] || die "unexpected output from the password hash script"
  env_set OMNISYNC_UI_PASSWORD_HASH "$hash" "$DIR/.env"
  env_set OMNISYNC_UI_SESSION_SECRET "$(random_hex 32)" "$DIR/.env"
  ok "web UI login set (only the hash is stored)"
}

write_env () {
  local file="$DIR/.env" token
  token=$(random_hex 32)
  if [ "$DRY_RUN" = 1 ]; then
    info "[dry-run] write $file (mode 0600): COMPOSE_PROJECT_NAME=$PROJECT OMNISYNC_VERSION=$VERSION OMNISYNC_API_TOKEN=<generated> PUID=$PUID PGID=$PGID TZ=$TZ_VALUE OMNISYNC_SYNC_DIR=$SYNC_DIR OMNISYNC_BIND_ADDRESS=$BIND_ADDRESS OMNISYNC_API_PORT=$API_PORT OMNISYNC_WEB_PORT=$WEB_PORT"
    return 0
  fi
  (
    umask 077
    cat >"$file.tmp.$$" <<EOF
# OmniSync settings, written by scripts/install.sh on $(date -u +%Y-%m-%d).
# Mode 0600: it holds the API token. After a change: docker compose up -d
# All settings: https://github.com/$REPO#environment-variables
COMPOSE_PROJECT_NAME=$PROJECT
OMNISYNC_VERSION=$VERSION
# The key of the API; the web UI's server adds it, osync needs it too
# (OMNISYNC_API_KEY).
OMNISYNC_API_TOKEN=$token
PUID=$PUID
PGID=$PGID
TZ=$TZ_VALUE
OMNISYNC_SYNC_DIR=$SYNC_DIR
OMNISYNC_BIND_ADDRESS=$BIND_ADDRESS
OMNISYNC_API_PORT=$API_PORT
OMNISYNC_WEB_PORT=$WEB_PORT
EOF
  )
  chmod 600 "$file.tmp.$$"
  mv -f "$file.tmp.$$" "$file"
  token=""
}

cmd_install () {
  local home want_pw=0 compose_tmp
  API_PORT=${API_PORT:-8000} WEB_PORT=${WEB_PORT:-3000}
  BIND_ADDRESS=${BIND_ADDRESS:-127.0.0.1} PROJECT=${PROJECT:-omnisync}
  [ -n "$DIR" ] || DIR=$(default_dir)
  check_path_value "--dir" "$DIR"
  [ ! -e "$DIR/.env" ] || die "$DIR/.env exists but $DIR is not a complete install (no compose.yml or no OMNISYNC_VERSION). Move it away or pick another --dir."

  preflight 1
  step "Installing OmniSync into $DIR"
  check_other_installs

  if [ -z "$VERSION" ]; then VERSION=$(latest_version); fi
  ! version_lt "$VERSION" "$MIN_VERSION" || die "this installer supports OmniSync $MIN_VERSION and later; see the README for $VERSION"
  info "version $VERSION ($RELEASES_URL/tag/v$VERSION)"

  make_tmp
  compose_tmp="$TMP/compose.yml"
  fetch_compose "$VERSION" "$compose_tmp"

  home=$(user_home)
  detect_ids
  TZ_VALUE=$(detect_timezone)
  if [ -z "$SYNC_DIR" ]; then
    SYNC_DIR=$(ask "Folder OmniSync may sync (profiles must lie inside it)" "$home/OmniSync")
  fi
  case $SYNC_DIR in \~|\~/*) SYNC_DIR="$home${SYNC_DIR#\~}" ;; /*) ;; *) SYNC_DIR="$PWD/$SYNC_DIR" ;; esac
  SYNC_DIR=${SYNC_DIR%/}
  check_path_value "the sync folder" "$SYNC_DIR"

  case $UI_PASSWORD in
    yes) want_pw=1 ;;
    ask)
      if [ "$YES" = 0 ] && [ "$DRY_RUN" = 0 ] && have_tty && confirm "Protect the web UI with a password? (needed before you open it to other machines)" n; then
        want_pw=1
      fi ;;
  esac
  if [ "$want_pw" = 1 ] && [ "$DRY_RUN" = 0 ]; then have_tty || die "--ui-password-prompt needs a terminal to read the password"; fi

  info "install folder  $DIR"
  info "sync folder     $SYNC_DIR"
  info "files owned by  $PUID:$PGID, time zone $TZ_VALUE"
  info "web UI          $(ui_url)"
  info "API             $(api_url)"
  info "data volume     ${PROJECT}_omnisync-data"
  confirm "Install OmniSync $VERSION with these settings?" y || die "stopped; nothing was changed"

  if [ ! -d "$DIR" ]; then run mkdir -p -m 700 "$DIR"; fi
  run cp "$compose_tmp" "$DIR/compose.yml"
  write_env
  ok "wrote $DIR/compose.yml and $DIR/.env (mode 0600)"
  if [ ! -d "$SYNC_DIR" ]; then
    run mkdir -p "$SYNC_DIR"
    if [ "$(id -u)" = 0 ]; then run chown "$PUID:$PGID" "$SYNC_DIR"; fi
  fi

  step "Pulling the images"
  test_release_active || run compose pull --quiet
  if [ "$want_pw" = 1 ]; then
    if [ "$DRY_RUN" = 1 ]; then info "[dry-run] ask for the web UI password and store its hash in .env"; else hash_ui_password; fi
  fi
  step "Starting OmniSync"
  run compose up -d
  if ! wait_healthy; then
    die "OmniSync did not become healthy. See the logs: cd \"$DIR\" && docker compose logs --tail 100"
  fi
  [ "$OSYNC" = 0 ] || install_osync "$VERSION"
  print_next_steps
}

print_next_steps () {
  [ "$DRY_RUN" = 0 ] || { step "Dry run: nothing was changed"; return 0; }
  cat <<EOF

${GREEN}OmniSync $VERSION is running.${RESET}

  Web UI      $(ui_url)
  API         $(api_url)   (token: OMNISYNC_API_TOKEN in $DIR/.env)
  Settings    $DIR/.env  (compose file: $DIR/compose.yml)
  Your files  $SYNC_DIR   (profile folders must lie inside it)
  Data        docker volume ${PROJECT}_omnisync-data (database, remotes, credentials)

Next steps:
  1. Open $(ui_url) and add a remote: Remotes -> Setup Wizard.
     Google Drive, OneDrive and Dropbox need your own OAuth app; register it
     with the redirect URI $(ui_url)/api/wizard/oauth/callback
     (step by step: https://github.com/$REPO/blob/main/docs/gem/remotes.md).
  2. Create a profile: a folder inside $SYNC_DIR and a remote folder.
  3. Later: rerun this installer to update, or run it with 'status' or 'uninstall'.
Guide: $DOCS_URL
EOF
}

# ---------------------------------------------------------------- update

backup_volume () {
  local old=$1 volume="${PROJECT}_omnisync-data" name stamp
  if [ "$NO_BACKUP" = 1 ]; then warn "--no-backup: updating without a backup of $volume"; return 0; fi
  if ! docker volume inspect "$volume" >/dev/null 2>&1 </dev/null; then
    warn "no data volume $volume to back up"
    return 0
  fi
  stamp=$(date +%Y%m%d-%H%M%S)
  name="omnisync-data-$old-$stamp.tar.gz"
  step "Backing up $volume to $DIR/backups/$name"
  run mkdir -p "$DIR/backups"
  run chmod 700 "$DIR/backups"
  # Stopped, so the database is consistent; the update starts it again. The
  # tar runs in the installed backend image (no other image needed).
  run compose stop backend
  if ! run docker run --rm --network none --entrypoint sh \
    -v "$volume:/data:ro" -v "$DIR/backups:/backup" \
    "${IMAGE_PREFIX}backend:$old" \
    -c 'tar czf "/backup/$1" -C /data . && chown "$2" "/backup/$1" && chmod 600 "/backup/$1"' \
    sh "$name" "$(id -u):$(id -g)" </dev/null; then
    run compose start backend || true
    die "the backup failed; nothing was updated (use --no-backup to update without one)"
  fi
  if [ "$DRY_RUN" = 0 ] && ! [ -s "$DIR/backups/$name" ]; then
    run compose start backend || true
    die "the backup $DIR/backups/$name is missing or empty; nothing was updated"
  fi
  ok "backup written (mode 0600; it holds your credentials)"
  BACKUP_FILE="$DIR/backups/$name"
}

ensure_running () {
  local backend frontend
  backend=$(container_state backend) frontend=$(container_state frontend)
  if [ "$backend" = healthy ] && [ "$frontend" = healthy ]; then return 0; fi
  step "Starting OmniSync (backend $backend, web UI $frontend)"
  run compose up -d
  wait_healthy || die "OmniSync did not become healthy. See the logs: cd \"$DIR\" && docker compose logs --tail 100"
}

cmd_update () {
  local target compose_tmp
  preflight 0
  find_install || die "no OmniSync install found in $DIR. Install it first (run this script without a command), or pass --dir."
  load_install
  target=$VERSION
  if [ -z "$target" ]; then target=$(latest_version); fi
  step "OmniSync in $DIR: installed $INSTALLED, latest $target"
  info "backend $(container_state backend), web UI $(container_state frontend)"
  if [ "$target" = "$INSTALLED" ]; then
    ok "up to date"
    ensure_running
    [ "$OSYNC" = 0 ] || install_osync "$INSTALLED"
    return 0
  fi
  if version_lt "$target" "$INSTALLED"; then
    die "$target is older than the installed $INSTALLED; downgrading is not supported (the database may have been migrated). Restore a backup from $DIR/backups instead."
  fi
  info "release notes: $RELEASES_URL/tag/v$target (read the upgrade notes before you continue)"
  confirm "Update OmniSync from $INSTALLED to $target?" y || die "stopped; nothing was changed"

  make_tmp
  compose_tmp="$TMP/compose.yml"
  fetch_compose "$target" "$compose_tmp"

  step "Pulling the $target images"
  if ! test_release_active; then
    run docker pull --quiet "${IMAGE_PREFIX}backend:$target" </dev/null
    run docker pull --quiet "${IMAGE_PREFIX}web:$target" </dev/null
  fi

  BACKUP_FILE=""
  backup_volume "$INSTALLED"

  step "Updating to $target"
  run cp "$DIR/compose.yml" "$DIR/compose.yml.previous"
  run cp "$compose_tmp" "$DIR/compose.yml"
  env_set OMNISYNC_VERSION "$target" "$DIR/.env"
  run compose up -d
  if wait_healthy; then
    run rm -f "$DIR/compose.yml.previous"
    VERSION=$target
    [ "$OSYNC" = 0 ] || install_osync "$target"
    ok "updated to $target"
    [ -z "$BACKUP_FILE" ] || info "backup of $INSTALLED's data: $BACKUP_FILE"
    return 0
  fi

  warn "$target did not become healthy: rolling back to $INSTALLED"
  run mv -f "$DIR/compose.yml.previous" "$DIR/compose.yml"
  env_set OMNISYNC_VERSION "$INSTALLED" "$DIR/.env"
  run compose up -d
  if wait_healthy; then
    die "the update to $target failed and $INSTALLED runs again. Its logs: cd \"$DIR\" && docker compose logs --tail 100"
  fi
  die "the update to $target failed, and $INSTALLED did not become healthy either (the database may already be migrated). Restore the backup ${BACKUP_FILE:-from $DIR/backups} as docs/gem/operations.md (\"Restore\") shows."
}

# ---------------------------------------------------------------- status

cmd_status () {
  local latest backend frontend volume
  preflight 0
  find_install || die "no OmniSync install found in $DIR (pass --dir for another folder)"
  load_install
  latest=$(latest_version 2>/dev/null) || latest="unknown (GitHub not reachable)"
  backend=$(container_state backend) frontend=$(container_state frontend)
  volume="${PROJECT}_omnisync-data"
  docker volume inspect "$volume" >/dev/null 2>&1 </dev/null || volume="$volume (missing)"
  printf 'OmniSync install in %s\n' "$DIR"
  printf '  installed     %s\n' "$INSTALLED"
  printf '  latest        %s\n' "$latest"
  printf '  backend       %s  (%s, /health)\n' "$backend" "$(api_url)"
  printf '  web UI        %s  (%s, /healthz)\n' "$frontend" "$(ui_url)"
  printf '  project       %s\n' "$PROJECT"
  printf '  data volume   %s\n' "$volume"
  printf '  sync folder   %s\n' "${SYNC_DIR:-(not set)}"
  printf '  settings      %s/.env\n' "$DIR"
  if [ -d "$DIR/backups" ]; then
    printf '  backups       %s/backups (%s)\n' "$DIR" "$(find "$DIR/backups" -name '*.tar.gz' -type f | wc -l | tr -d ' ')"
  fi
  if is_version "$latest" && version_lt "$INSTALLED" "$latest"; then
    printf '\nUpdate available: rerun the installer (or run it with "update"). Notes: %s/tag/v%s\n' "$RELEASES_URL" "$latest"
  fi
}

# ---------------------------------------------------------------- uninstall

cmd_uninstall () {
  local volume answer
  preflight 0
  find_install || die "no OmniSync install found in $DIR (pass --dir for another folder)"
  load_install
  volume="${PROJECT}_omnisync-data"
  if [ "$PURGE" = 0 ]; then
    confirm "Stop and remove OmniSync's containers in $DIR? (the data volume and .env stay)" y || die "stopped; nothing was changed"
    step "Removing the containers"
    run compose down
    ok "removed. Kept: the data volume $volume, $DIR/.env and $DIR/compose.yml."
    info "Start it again: cd \"$DIR\" && docker compose up -d. Delete everything: rerun with uninstall --purge."
    return 0
  fi

  warn "--purge deletes the data volume $volume: profiles, job history, your remotes' credentials (rclone.conf) and the API token. Your synced files and $DIR/backups stay."
  if have_tty && [ "$DRY_RUN" = 0 ]; then
    printf 'Type the volume name (%s) to delete it: ' "$volume" >/dev/tty
    IFS= read -r answer </dev/tty || answer=""
    [ "$answer" = "$volume" ] || die "not confirmed; nothing was deleted"
  elif [ "$YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
    die "no terminal to confirm --purge; rerun with --purge --yes to delete $volume without asking"
  fi
  step "Removing the containers and the data volume"
  run compose down --volumes
  run rm -f "$DIR/compose.yml" "$DIR/compose.yml.previous" "$DIR/.env"
  if [ "$DRY_RUN" = 0 ]; then rmdir "$DIR" 2>/dev/null || info "kept $DIR (it holds other files, e.g. backups/)"; fi
  ok "purged. Your files in ${SYNC_DIR:-the sync folder} are untouched."
  info "The images stay; remove them with: docker image rm ${IMAGE_PREFIX}backend:$INSTALLED ${IMAGE_PREFIX}web:$INSTALLED"
}

# ---------------------------------------------------------------- osync

install_osync () {
  local v=$1 name dest file
  name="osync-$OS-$ARCH"
  if [ "$(id -u)" = 0 ]; then dest=/usr/local/bin; else dest="$HOME/.local/bin"; fi
  step "Installing osync $v to $dest/osync"
  make_tmp
  fetch_sums "$v"
  file="$TMP/$v/$name"
  download "$RELEASES_URL/download/v$v/$name" "$file" || die "could not download $name of release $v"
  check_sum "$v" "$name" "$file"
  ok "$name matches SHA256SUMS"
  run mkdir -p "$dest"
  run cp "$file" "$dest/osync"
  run chmod 755 "$dest/osync"
  case ":$PATH:" in
    *":$dest:"*) ;;
    *) info "$dest is not on your PATH; add it, e.g. in ~/.profile: export PATH=\"$dest:\$PATH\"" ;;
  esac
  info "osync needs the API token: export OMNISYNC_API_KEY=\"\$(grep '^OMNISYNC_API_TOKEN=' '$DIR/.env' | cut -d= -f2-)\""
  if [ "$API_PORT" != 8000 ] || [ "$BIND_ADDRESS" != 127.0.0.1 ]; then info "and the address: export OMNISYNC_URL=$(api_url)"; fi
}

# ---------------------------------------------------------------- main

# The compose file for releases that attach none (before 0.12.0); the same
# as deploy/compose.yml (a test checks that).
embedded_compose () {
  cat <<'COMPOSE'
# OmniSync from the released images: the backend API and the web UI.
#
# scripts/install.sh downloads this file (a release asset, listed in the
# release's SHA256SUMS) and writes the .env next to it; to set it up by hand,
# see the README's "Install a release". It is the repository's
# docker-compose.yml with the images in place of the builds; that file's
# comments explain each setting.
#
# Settings go in .env next to this file:
#   OMNISYNC_VERSION     the release to run (both images use the same one)
#   OMNISYNC_API_TOKEN   the API key the web UI's server and osync send
#   OMNISYNC_SYNC_DIR    the host folder profiles may sync (absolute path)
#   PUID, PGID, TZ       the user and group files are written as; time zone
#   OMNISYNC_BIND_ADDRESS  the host address of the API port (default 127.0.0.1)
#   OMNISYNC_API_PORT, OMNISYNC_WEB_PORT  host ports (default 8000 and 3000)
#   OMNISYNC_UI_PASSWORD_HASH, OMNISYNC_UI_SESSION_SECRET  the web UI login
#   OMNISYNC_LOG_FORMAT, OMNISYNC_LOG_MAX_BYTES, OMNISYNC_LOG_BACKUPS,
#   OMNISYNC_LOG_ACCESS  the backend's log (docs/gem/operations.md, "Logs")
services:
  backend:
    image: ghcr.io/pan-fire/omnisync-backend:${OMNISYNC_VERSION:?set OMNISYNC_VERSION in .env}
    ports:
      - "${OMNISYNC_BIND_ADDRESS:-127.0.0.1}:${OMNISYNC_API_PORT:-8000}:8000"
    environment:
      - PUID=${PUID:-1000}
      - PGID=${PGID:-1000}
      - TZ=${TZ:-UTC}
      - OMNISYNC_LOG_FORMAT=${OMNISYNC_LOG_FORMAT:-}
      - OMNISYNC_LOG_MAX_BYTES=${OMNISYNC_LOG_MAX_BYTES:-}
      - OMNISYNC_LOG_BACKUPS=${OMNISYNC_LOG_BACKUPS:-}
      - OMNISYNC_LOG_ACCESS=${OMNISYNC_LOG_ACCESS:-}
      - OMNISYNC_HOST_OS=linux
      - OMNISYNC_API_TOKEN=${OMNISYNC_API_TOKEN:-}
      - OMNISYNC_ALLOWED_HOSTS=backend,${OMNISYNC_ALLOWED_HOSTS:-}
      - OMNISYNC_BROWSE_ROOTS=${OMNISYNC_SYNC_DIR:-/sync}
    volumes:
      - omnisync-data:/data/omnisync
      - ${OMNISYNC_SYNC_DIR:-./sync}:${OMNISYNC_SYNC_DIR:-/sync}
    security_opt:
      - no-new-privileges:true
    cap_drop: [ALL]
    cap_add: [CHOWN, DAC_OVERRIDE, SETUID, SETGID]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
    stop_grace_period: 90s

  frontend:
    image: ghcr.io/pan-fire/omnisync-web:${OMNISYNC_VERSION:?set OMNISYNC_VERSION in .env}
    ports:
      - "127.0.0.1:${OMNISYNC_WEB_PORT:-3000}:3000"
    environment:
      - BACKEND_URL=http://backend:8000
      - OMNISYNC_API_TOKEN=${OMNISYNC_API_TOKEN:-}
      - OMNISYNC_UI_ALLOWED_HOSTS=${OMNISYNC_UI_ALLOWED_HOSTS:-}
      - OMNISYNC_UI_PASSWORD_HASH=${OMNISYNC_UI_PASSWORD_HASH:-}
      - OMNISYNC_UI_PASSWORD=${OMNISYNC_UI_PASSWORD:-}
      - OMNISYNC_UI_SESSION_SECRET=${OMNISYNC_UI_SESSION_SECRET:-}
      - OMNISYNC_UI_SESSION_DAYS=${OMNISYNC_UI_SESSION_DAYS:-}
      - TZ=${TZ:-UTC}
    depends_on:
      - backend
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop: [ALL]
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped

volumes:
  omnisync-data:
COMPOSE
}

main () {
  parse_args "$@"
  if [ "$COMMAND" = help ]; then usage; return 0; fi
  if [ -n "$DIR" ]; then
    case $DIR in \~|\~/*) DIR="$HOME${DIR#\~}" ;; /*) ;; *) DIR="$PWD/$DIR" ;; esac
    DIR=${DIR%/}
  fi
  [ "$DRY_RUN" = 0 ] || step "Dry run: printing what would be done, changing nothing"
  test_release_check

  case $COMMAND in
    install)
      if find_install; then
        load_install
        step "OmniSync $INSTALLED is already installed in $DIR"
        info "Update it with 'update', check it with 'status'."
        [ "$OSYNC" = 0 ] || install_osync "$INSTALLED"
        return 0
      fi
      cmd_install ;;
    update) cmd_update ;;
    status) cmd_status ;;
    uninstall) cmd_uninstall ;;
    "")
      if find_install; then cmd_update; else cmd_install; fi ;;
  esac
}

main "$@"
