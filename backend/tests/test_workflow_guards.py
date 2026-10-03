"""The GitHub workflows run only on GitHub-hosted runners and stay least-privilege.

Every job runs on a GitHub-hosted runner: a fresh VM per job, where a pull
request from a fork gets a read-only token and no secrets. That is what makes
it safe to run CI for forks, so a job may not move to a self-hosted runner
(which would keep state between jobs and could reach other machines), and no
workflow may use ``pull_request_target`` (which runs a fork's pull request
with secrets and a write token). Also checks that every workflow declares
its token ``permissions:``. See ci.yml and CONTRIBUTING.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
# GitHub-hosted runner images the workflows may name; pinned versions, so an
# image upgrade is a deliberate change.
GITHUB_HOSTED = {"ubuntu-24.04", "ubuntu-24.04-arm"}
# A matrix value used as runs-on: its values are checked in the matrix.
_MATRIX_RUNNER = re.compile(r"^\$\{\{\s*matrix\.([\w.]+)\s*\}\}$")


def _workflows() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    # YAML 1.1 reads the key `on` as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _triggers(data: dict) -> set[str]:
    on = data.get("on")
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on or {})


def _runner_labels(path: Path, job: dict) -> list[str]:
    """The labels a job's runs-on can resolve to, as far as the file shows them."""
    runs_on = job["runs-on"]
    if isinstance(runs_on, list):
        return [str(label) for label in runs_on]
    runs_on = str(runs_on)
    match = _MATRIX_RUNNER.match(runs_on)
    if not match:
        return [runs_on]
    # matrix.<key>[.<field>] taken from the matrix's literal values, or, for
    # a computed matrix, from the `runners = {...}` table of the step that
    # computes it (release.yml's platform list).
    key, *field = match.group(1).split(".")
    values = job.get("strategy", {}).get("matrix", {}).get(key)
    if isinstance(values, list):
        return [str(v[field[0]] if field else v) for v in values]
    table = re.search(r"runners = \{([^}]*)\}", path.read_text())
    return sorted(set(re.findall(r':\s*"([^"]+)"', table.group(1)))) if table else []


def test_there_are_workflows():
    assert len(_workflows()) >= 4


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_jobs_run_on_github_hosted_runners(path):
    jobs = _load(path)["jobs"]
    for name, job in jobs.items():
        if "uses" in job:  # a reusable workflow; its own file is checked
            continue
        labels = _runner_labels(path, job)
        assert labels, f"{path.name}: job {name} has a runs-on this test cannot resolve"
        others = [label for label in labels if label not in GITHUB_HOSTED]
        assert others == [], (
            f"{path.name}: job {name} runs on {others}; only GitHub-hosted runners "
            f"({sorted(GITHUB_HOSTED)}) may run this repository's jobs, fork pull requests included."
        )


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_no_pull_request_target(path):
    """pull_request_target runs a fork's pull request with secrets and a write token."""
    assert "pull_request_target" not in _triggers(_load(path))


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_every_workflow_sets_token_permissions(path):
    data = _load(path)
    assert "permissions" in data, f"{path.name} has no top-level permissions:"
    assert data["permissions"] not in ("write-all", "read-all")


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_pull_request_workflows_default_to_read_only(path):
    """A fork's pull request gets a read-only token anyway; ours start read-only too."""
    data = _load(path)
    if "pull_request" not in _triggers(data):
        return
    assert data["permissions"] == {"contents": "read"}, f"{path.name}: top-level permissions must be contents: read"


def test_the_runner_check_resolves_matrices(tmp_path):
    workflow = tmp_path / "w.yml"
    workflow.write_text('runners = {"linux/amd64": "ubuntu-24.04", "linux/arm64": "my-arm-box"}\n')
    computed = {"runs-on": "${{ matrix.target.runner }}", "strategy": {"matrix": {"target": "${{ x }}"}}}
    assert _runner_labels(workflow, computed) == ["my-arm-box", "ubuntu-24.04"]
    job = {
        "runs-on": "${{ matrix.target.runner }}",
        "strategy": {"matrix": {"target": [{"runner": "ubuntu-24.04"}, {"runner": "self-hosted"}]}},
    }
    assert _runner_labels(workflow, job) == ["ubuntu-24.04", "self-hosted"]
    assert _runner_labels(workflow, {"runs-on": ["self-hosted", "docker"]}) == ["self-hosted", "docker"]
    assert _runner_labels(workflow, {"runs-on": "ubuntu-24.04"}) == ["ubuntu-24.04"]
