#!/usr/bin/env bash
# Prepare a release: bump the version and turn the changelog's Unreleased
# section into the new version's notes. It changes files only; it prints the
# git commands that commit, tag and push, and runs none of them.
#
#   scripts/release/prepare.sh 0.10.0              # today's date
#   scripts/release/prepare.sh 1.0.0-rc.1 --date 2026-10-01
#
# What it changes:
#   VERSION                the single version source (backend, images, osync)
#   frontend/package.json  its "version", which the web UI shows
#   CHANGELOG.md           "## [Unreleased]" keeps its heading and becomes
#                          empty; its entries move under "## [<version>] -
#                          <date>", and the links at the end follow (a
#                          compare link from the previous release, or the
#                          tag page for the first release)
#
# It refuses a version that is not semver, not newer than VERSION, or already
# in the changelog, an empty Unreleased section, and (in a git checkout)
# uncommitted changes, unless --allow-dirty.
#
# Pushing the tag starts .github/workflows/release.yml, which checks the tag
# against VERSION and publishes the images, binaries and GitHub release.
#
# OMNISYNC_RELEASE_ROOT overrides the repository root (for tests).
set -euo pipefail

usage () {
  echo "usage: $0 <version> [--date YYYY-MM-DD] [--allow-dirty]" >&2
  exit 2
}

version=""
date=$(date -u +%Y-%m-%d)
allow_dirty=0
while [ $# -gt 0 ]; do
  case "$1" in
    --date) [ $# -ge 2 ] || usage; date=$2; shift 2 ;;
    --allow-dirty) allow_dirty=1; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) [ -z "$version" ] || usage; version=${1#v}; shift ;;
  esac
done
[ -n "$version" ] || usage

root=${OMNISYNC_RELEASE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}
cd "$root"

for f in VERSION CHANGELOG.md frontend/package.json; do
  [ -f "$f" ] || { echo "missing $root/$f" >&2; exit 1; }
done
[[ $date =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "--date must be YYYY-MM-DD, got '$date'" >&2; exit 1; }

if [ "$allow_dirty" = 0 ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && [ -n "$(git status --porcelain)" ]; then
  echo "the working tree has uncommitted changes; commit them first (or pass --allow-dirty)" >&2
  exit 1
fi

current=$(tr -d '[:space:]' < VERSION)

# The checks and the edits, in one place. Nothing is written unless every
# check passes.
python3 - "$current" "$version" "$date" <<'PY'
import json
import re
import sys
from pathlib import Path

current, new, date = sys.argv[1:4]

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?$"
)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def key(version: str):
    """Semver precedence: a pre-release sorts before its release."""
    m = SEMVER.match(version)
    core = tuple(int(m.group(i)) for i in (1, 2, 3))
    if m.group(4) is None:
        return core, (1,)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split("."))
    return core, (0, parts)


if not SEMVER.match(new):
    fail(f"'{new}' is not a semantic version (e.g. 1.2.3 or 1.2.3-rc.1)")
if not SEMVER.match(current):
    fail(f"VERSION holds '{current}', which is not a semantic version")
if key(new) <= key(current):
    fail(f"{new} is not newer than the current version {current}")

changelog = Path("CHANGELOG.md")
text = changelog.read_text(encoding="utf-8")
if re.search(rf"^## \[{re.escape(new)}\]", text, re.M):
    fail(f"CHANGELOG.md already has a section for {new}")

m = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.M)
if not m:
    fail("CHANGELOG.md has no '## [Unreleased]' section")
start = m.end()
nxt = re.search(r"^## \[", text[start:], re.M)
end = start + nxt.start() if nxt else len(text)
# The section's lines, apart from the link references that end the file
# when Unreleased is the only section.
is_link = re.compile(r"^\[[^\]]+\]: ").match
lines = text[start:end].splitlines()
entries = "\n".join(l for l in lines if not is_link(l)).strip()
links = "\n".join(l for l in lines if is_link(l))
if not re.search(r"^\s*[-*] ", entries, re.M):
    fail("the Unreleased section has no entries to release")

section = f"\n## [{new}] - {date}\n\n{entries}\n\n"
text = text[:start] + section + (links + "\n" if links else "") + text[end:]

# Links: [Unreleased] now starts at the new tag. The new version spans from
# the previous release in the changelog; the first release (no earlier
# version section, so no earlier tag) links to its own tag page instead.
link = re.search(r"^\[Unreleased\]: (https://github\.com/[^/\s]+/[^/\s]+)(?:/\S*)?$", text, re.M)
if link:
    base = link.group(1)
    previous = re.search(r"^## \[(?!Unreleased\])(?!" + re.escape(new) + r"\])([^\]]+)\]", text, re.M)
    version_link = (f"{base}/compare/v{previous.group(1)}...v{new}" if previous
                    else f"{base}/releases/tag/v{new}")
    text = (text[:link.start()]
            + f"[Unreleased]: {base}/compare/v{new}...HEAD\n"
            + f"[{new}]: {version_link}"
            + text[link.end():])

package = Path("frontend/package.json")
raw = package.read_text(encoding="utf-8")
if json.loads(raw).get("version") is None:
    fail("frontend/package.json has no version field")
# Replace in place so the file's formatting stays as it is.
raw = re.sub(r'^(  "version":\s*")[^"]*(")', lambda m: m.group(1) + new + m.group(2), raw, count=1, flags=re.M)
if json.loads(raw)["version"] != new:
    fail("could not update frontend/package.json's version")

changelog.write_text(text, encoding="utf-8")
package.write_text(raw, encoding="utf-8")
Path("VERSION").write_text(new + "\n", encoding="utf-8")
PY

cat <<EOF
Prepared OmniSync $version (was $current), dated $date:
  VERSION, frontend/package.json and CHANGELOG.md are updated.

Review the release notes:
  scripts/release/changelog-notes.sh $version

Then commit, get the commit onto main (through a pull request if main is
protected), and tag that commit on main. Pushing the tag starts the release:

  git add VERSION CHANGELOG.md frontend/package.json
  git commit -m "chore(release): v$version"
  git push origin HEAD            # or open a pull request and merge it

  git fetch origin && git checkout main && git pull --ff-only
  git tag -a v$version -m "OmniSync $version"
  git push origin v$version

Nothing was committed, tagged or pushed.
EOF
