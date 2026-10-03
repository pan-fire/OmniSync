"""No credentials in the committed files.

Scans every tracked file with OmniSync's own gitleaks rules (the
``omnisync-*`` [[rules]] of .gitleaks.toml: private keys, rclone OAuth
tokens, client secrets with a literal value, AWS key ids), honouring the
same reviewed allow-lists. CI also runs gitleaks itself over the history
(.github/workflows/secrets.yml); this test catches a secret before a
commit is even made, with nothing but Python.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / ".gitleaks.toml"


@dataclass(frozen=True)
class Rule:
    id: str
    regex: re.Pattern[str]
    allow_regexes: tuple[re.Pattern[str], ...]
    allow_paths: tuple[re.Pattern[str], ...]


def _allow(entries: list[dict]) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    regexes = tuple(re.compile(r) for e in entries for r in e.get("regexes", []))
    paths = tuple(re.compile(p) for e in entries for p in e.get("paths", []))
    return regexes, paths


def load_rules(config: Path = CONFIG) -> list[Rule]:
    data = tomllib.loads(config.read_text())
    global_regexes, global_paths = _allow(data.get("allowlists", []))
    rules = []
    for raw in data.get("rules", []):
        if not raw["id"].startswith("omnisync-"):
            continue
        regexes, paths = _allow(raw.get("allowlists", []))
        rules.append(Rule(
            id=raw["id"], regex=re.compile(raw["regex"]),
            allow_regexes=global_regexes + regexes, allow_paths=global_paths + paths,
        ))
    return rules


def _line_of(text: str, start: int, end: int) -> str:
    """The whole line(s) a match spans (allow-lists here target the line)."""
    first = text.rfind("\n", 0, start) + 1
    last = text.find("\n", end)
    return text[first:] if last == -1 else text[first:last]


def find_secrets(path: str, text: str, rules: list[Rule]) -> list[tuple[str, str, int]]:
    """(rule id, path, line number) of each finding not allow-listed."""
    findings = []
    for rule in rules:
        if any(p.search(path) for p in rule.allow_paths):
            continue
        for match in rule.regex.finditer(text):
            line = _line_of(text, match.start(), match.end())
            if any(a.search(line) or a.search(match.group(0)) for a in rule.allow_regexes):
                continue
            findings.append((rule.id, path, text.count("\n", 0, match.start()) + 1))
    return findings


def _tracked_files() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in result.stdout.decode().split("\0") if p]


def test_the_rules_load_and_cover_the_risky_patterns():
    ids = {rule.id for rule in load_rules()}
    assert ids == {
        "omnisync-private-key", "omnisync-rclone-oauth-token",
        "omnisync-client-secret", "omnisync-aws-access-key-id",
    }


# Built from pieces so this file does not hold a match itself.
_BEGIN = "-----" + "BEGIN"


@pytest.mark.parametrize("rule_id, sample", [
    ("omnisync-private-key", f"{_BEGIN} OPENSSH PRIVATE KEY-----\nb3Blbg==\n"),
    ("omnisync-private-key", f'KEY = "{_BEGIN} EC PRIVATE KEY-----"\n'),
    ("omnisync-rclone-oauth-token", 'token = {"access' + '_token":"ya29.a0Realistic","expiry":"x"}\n'),
    ("omnisync-rclone-oauth-token", 'conf = "token = {\\"access' + '_token\\":\\"ya29.a0Escaped\\"}"\n'),
    ("omnisync-client-secret", "client_secret = Zx9q" + "LongEnoughValue\n"),
    ("omnisync-client-secret", '    client_secret = "q8W3' + 'literalValue"\n'),
    ("omnisync-client-secret", '    "client_secret": "q8W3' + 'literalValue",\n'),
    ("omnisync-client-secret", "GDRIVE_CLIENT_SECRET=Zx9q" + "LongEnoughValue\n"),
    ("omnisync-aws-access-key-id", 'key = "AKIA' + 'Z7QX3KEYQ2ABCDEF"\n'),
])
def test_each_rule_finds_its_pattern(rule_id, sample):
    assert [f[0] for f in find_secrets("x.py", sample, load_rules())] == [rule_id]


@pytest.mark.parametrize("sample", [
    "client_secret = (request.client_secret or '').strip() or None\n",
    "        client_secret=session.client_secret,\n",
    '            "client_secret": client_secret,\n',
    "    client_secret: str = ''\n",
    'token = {"access_token":"x"}\n',  # too short to be real
    '"access_key_id": "AKIAIOSF' + 'ODNN7EXAMPLE",\n',  # AWS's documented example
    "-----END OPENSSH PRIVATE KEY-----\n",
])
def test_code_and_allow_listed_values_are_not_findings(sample):
    assert find_secrets("x.py", sample, load_rules()) == []


def test_no_secrets_in_tracked_files():
    rules = load_rules()
    findings = []
    for path in _tracked_files():
        file = REPO / path
        if not file.is_file() or file.is_symlink():
            continue  # deleted in the work tree, or a link to elsewhere
        data = file.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        findings += find_secrets(path, data.decode("utf-8", errors="replace"), rules)
    assert findings == [], (
        "Possible secrets in tracked files (rule, file, line). Remove them, or, if one is "
        "public or fake, add a reviewed allow-list entry with its reason to .gitleaks.toml."
    )
