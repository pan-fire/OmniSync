#!/usr/bin/env python3
"""Create or update the GitHub release for a tag and attach its files.

    publish_github_release.py --tag v0.10.0 --notes notes.md [--prerelease] FILE...

Used by .github/workflows/release.yml. Plain REST calls with the standard
library, so it needs nothing but Python.

The release is created as a draft, the files are uploaded, and only then is
it published, so nobody sees a release with half its downloads. A re-run
(e.g. after a failed upload) finds the existing release, draft or not,
replaces its notes and any file of the same name, and publishes it.

Environment: GH_TOKEN (contents: write), GITHUB_REPOSITORY (owner/name) and
GITHUB_API_URL (set by Actions; default https://api.github.com).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class GitHub:
    def __init__(self, api: str, repo: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.repo = repo
        self.token = token

    def request(self, method: str, url: str, body: object | None = None,
                data: bytes | None = None, content_type: str = "application/json") -> Any:
        if not url.startswith("http"):
            url = f"{self.api}/repos/{self.repo}{url}"
        if body is not None:
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise SystemExit(f"{method} {url}: HTTP {exc.code}: {detail}") from None
        return json.loads(raw) if raw else None

    def find_release(self, tag: str) -> dict | None:
        """The release for tag, drafts included (GET /releases/tags/ skips drafts)."""
        page = 1
        while True:
            releases = self.request("GET", f"/releases?per_page=100&page={page}")
            for release in releases:
                if release["tag_name"] == tag:
                    return release
            if len(releases) < 100:
                return None
            page += 1


def publish(gh: GitHub, tag: str, title: str, notes: str, prerelease: bool, files: list[Path]) -> dict:
    fields = {"name": title, "body": notes, "prerelease": prerelease}
    release = gh.find_release(tag)
    if release is None:
        release = gh.request("POST", "/releases", {"tag_name": tag, "draft": True, **fields})
        print(f"created draft release {tag}")
    else:
        release = gh.request("PATCH", f"/releases/{release['id']}", fields)
        print(f"updating existing release {tag}")

    existing = {a["name"]: a["id"] for a in release.get("assets", [])}
    upload_base = re.sub(r"\{.*\}$", "", release["upload_url"])
    for path in files:
        if path.name in existing:
            gh.request("DELETE", f"/releases/assets/{existing[path.name]}")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        url = f"{upload_base}?{urllib.parse.urlencode({'name': path.name})}"
        gh.request("POST", url, data=path.read_bytes(), content_type=ctype)
        print(f"  uploaded {path.name}")

    # Only prereleases stay out of "latest" (the /releases/latest/download
    # links in the docs).
    release = gh.request("PATCH", f"/releases/{release['id']}", {
        "draft": False, "make_latest": "false" if prerelease else "true",
    })
    print(f"published {release.get('html_url', tag)}")
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title")
    parser.add_argument("--notes", required=True, type=Path, help="Markdown file with the release notes")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GH_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        return 2
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        print(f"missing files: {', '.join(missing)}", file=sys.stderr)
        return 2

    gh = GitHub(os.environ.get("GITHUB_API_URL", "https://api.github.com"), repo, token)
    title = args.title or f"OmniSync {args.tag.removeprefix('v')}"
    publish(gh, args.tag, title, args.notes.read_text(encoding="utf-8"), args.prerelease, args.files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
