#!/usr/bin/env bash
# Print the CHANGELOG.md section for one version: the release notes.
#
#   scripts/release/changelog-notes.sh 0.10.0 [CHANGELOG.md]
#
# Prints the lines between "## [0.10.0]" and the next "## [" heading, without
# the heading itself and without leading or trailing blank lines. Fails when
# the section is missing or empty, so a tag cannot be released without notes
# (.github/workflows/release.yml).
set -euo pipefail

version=${1:?usage: changelog-notes.sh <version> [changelog]}
changelog=${2:-$(cd "$(dirname "$0")/../.." && pwd)/CHANGELOG.md}

[ -f "$changelog" ] || { echo "no changelog at $changelog" >&2; exit 1; }

notes=$(awk -v v="$version" '
  /^## \[/ {
    if (found) exit
    # "## [0.10.0] - 2026-10-02": the text between the brackets.
    h = $0; sub(/^## \[/, "", h); sub(/\].*$/, "", h)
    if (h == v) { found = 1; next }
  }
  # The link references at the end of the file belong to no section.
  found && !/^\[[^]]+\]: / { print }
  END { if (!found) exit 3 }
' "$changelog") || { echo "CHANGELOG.md has no section for $version" >&2; exit 1; }

# Drop leading blank lines; $(...) already dropped the trailing ones.
notes=$(printf '%s\n' "$notes" | sed '/./,$!d')
if [ -z "$notes" ]; then
  echo "the CHANGELOG.md section for $version is empty" >&2
  exit 1
fi
printf '%s\n' "$notes"
