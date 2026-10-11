"""Hardening of caller-supplied input.

- remote names never reach rclone as options (422, rclone not run);
- local folders stay out of OmniSync's data directory and, when
  OMNISYNC_BROWSE_ROOTS is set, inside it (new values only);
- rclone_args: switches take no separate value;
- weak tokens and failed logins are logged, repeated failures throttled;
- request sizes are bounded.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from backend import security
from backend.api.schemas import (
    MAX_RCLONE_ARGS,
    MAX_RCLONE_FILTER_RULES,
    SAFE_RCLONE_BOOL_FLAGS,
    SAFE_RCLONE_VALUE_FLAGS,
    BackupTargetType,
    CreateRemoteRequest,
    ProfileCreateRequest,
    PushSubscriptionRequest,
    SelectiveSyncRequest,
    check_backup_target,
    check_rclone_args,
)
from backend.api.schemas import (
    TestSyncRequest as SyncTestRequest,
)
from backend.db.models import BackupTarget, Conflict, SyncProfile
from backend.exceptions import RcloneError
from backend.main import app
from backend.services import path_guard
from backend.services.rclone import RcloneResult, RcloneService
from backend.tests.auth import AUTH_HEADERS
from backend.tests.test_profiles import env  # noqa: F401  (fixture)

BAD_REMOTE_NAMES = [
    "--log-file=/tmp/x",
    "-v",
    "--config",
    "a=b",
    "has space",
    "dot.ted",
    "semi;colon",
    "",
]

VALID_PROFILE = {"name": "Docs", "local_dir": "/home/me/Docs", "remote_dir": "gdrive:Docs"}


def _ok_result(stdout: str = "") -> RcloneResult:
    return RcloneResult(stdout=stdout, stderr="", return_code=0, elapsed_seconds=0.0)


# --- 1. Remote names never reach rclone as options ---


@pytest.mark.parametrize("name", [n for n in BAD_REMOTE_NAMES if n])
async def test_browse_remote_rejects_bad_remote_name(test_client, test_services, name):
    test_services.rclone._run = AsyncMock(return_value=_ok_result())
    resp = await test_client.get("/browse/remote", params={"path": f"{name}:sub"})
    assert resp.status_code == 422, resp.text
    test_services.rclone._run.assert_not_called()


async def test_browse_remote_passes_the_path_after_double_dash(test_client, test_services):
    test_services.rclone._run = AsyncMock(return_value=_ok_result(
        "          -1 2024-01-15 10:30:45        -1 Photos\n"
    ))
    resp = await test_client.get("/browse/remote", params={"path": "gdrive:--log-file=x"})
    assert resp.status_code == 200, resp.text
    args, kwargs = test_services.rclone._run.call_args
    assert kwargs["positional"] == ["gdrive:--log-file=x"]
    assert not any("gdrive" in a for a in args[0])


@pytest.mark.parametrize("name", [n for n in BAD_REMOTE_NAMES if n and "/" not in n])
@pytest.mark.parametrize("route", ["test", "about"])
async def test_remote_routes_reject_bad_names(test_client, test_services, name, route):
    test_services.rclone._run = AsyncMock(return_value=_ok_result())
    test_services.rclone.about = AsyncMock(return_value={})
    method = test_client.post if route == "test" else test_client.get
    resp = await method(f"/remotes/{name}/{route}")
    assert resp.status_code == 422, resp.text
    test_services.rclone._run.assert_not_called()
    test_services.rclone.about.assert_not_called()


async def test_remote_test_passes_name_after_double_dash(test_client, test_services):
    test_services.rclone._run = AsyncMock(return_value=_ok_result())
    test_services.rclone.list_remotes = AsyncMock(return_value=[])
    resp = await test_client.post("/remotes/gdrive/test")
    assert resp.status_code == 200
    args, kwargs = test_services.rclone._run.call_args
    assert args[0] == ["lsd"] and kwargs["positional"] == ["gdrive:"]


@pytest.mark.parametrize("name", BAD_REMOTE_NAMES)
async def test_wizard_test_rejects_bad_names(test_client, test_services, name):
    test_services.rclone.check_remote = AsyncMock(return_value=True)
    resp = await test_client.post("/wizard/test", json={"name": name})
    assert resp.status_code == 422, resp.text
    test_services.rclone.check_remote.assert_not_called()


async def test_rclone_about_puts_remote_after_double_dash(tmp_path):
    svc = RcloneService(rclone_config_path=str(tmp_path / "r.conf"))
    with patch.object(svc, "_run", AsyncMock(return_value=_ok_result('{"total": 1}'))) as run:
        assert (await svc.about("gdrive"))["total"] == 1
    args, kwargs = run.call_args
    assert args[0] == ["about", "--json"] and kwargs["positional"] == ["gdrive:"]
    with pytest.raises(ValueError):
        await svc.about("--log-file=/tmp/x")


async def test_rclone_check_remote_fallback_uses_double_dash(tmp_path):
    svc = RcloneService(rclone_config_path=str(tmp_path / "r.conf"))
    with patch.object(svc, "_run", AsyncMock(return_value=_ok_result())) as run:
        await svc._check_remote_via_rclone("gdrive")
    assert run.call_args.kwargs["positional"] == ["gdrive:"]
    assert all("gdrive" not in a for a in run.call_args.args[0])


async def test_rclone_test_sync_puts_remote_specs_after_double_dash(tmp_path):
    svc = RcloneService(rclone_config_path=str(tmp_path / "r.conf"))
    with patch.object(svc, "_run", AsyncMock(return_value=_ok_result())) as run:
        result = await svc.test_sync(str(tmp_path / "local"), "nas:backup")
    assert result["success"], result
    calls = {c.args[0][0]: c for c in run.call_args_list}
    assert set(calls) == {"copyto", "lsf", "deletefile"}
    for call in calls.values():
        assert call.args[0] == [call.args[0][0]]  # only the subcommand before "--"
        assert any(p.startswith("nas:backup/.omnisync-test-") for p in call.kwargs["positional"])


async def test_rclone_test_sync_refuses_bad_remote_name(tmp_path):
    svc = RcloneService(rclone_config_path=str(tmp_path / "r.conf"))
    with patch.object(svc, "_run", AsyncMock(return_value=_ok_result())) as run:
        result = await svc.test_sync(str(tmp_path / "local"), "--log-file=/tmp/x:y")
    assert not result["success"]
    run.assert_not_called()


async def test_real_rclone_does_not_read_a_remote_path_as_an_option(tmp_path):
    """End to end against the installed rclone: '--log-file=...' after '--' is a path."""
    import shutil

    if shutil.which("rclone") is None:
        if os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
            pytest.fail("rclone is required")
        pytest.skip("rclone not installed")
    (tmp_path / "r.conf").write_text("[loc]\ntype = local\n")
    svc = RcloneService(rclone_config_path=str(tmp_path / "r.conf"))
    victim = tmp_path / "victim.log"
    with pytest.raises(RcloneError):  # no such folder; what matters is the log file
        await svc._run(["lsd"], use_config_args=False, timeout=15, positional=[f"loc:--log-file={victim}"])
    assert not victim.exists()


@pytest.mark.parametrize("body", [
    {"local_dir": "relative", "remote_dir": "gdrive:x"},
    {"local_dir": "--log-file=/tmp/x", "remote_dir": "gdrive:x"},
    {"local_dir": "/home/me/x", "remote_dir": "--config=/tmp/evil:x"},
    {"local_dir": "/home/me/x", "remote_dir": "-v:x"},
    {"local_dir": "/home/me/x", "remote_dir": "no-colon"},
])
async def test_test_sync_request_is_validated(test_client, test_services, body):
    test_services.rclone.test_sync = AsyncMock(return_value={"success": True, "steps": []})
    resp = await test_client.post("/config/test-sync", json=body)
    assert resp.status_code == 422, resp.text
    test_services.rclone.test_sync.assert_not_called()


# --- 2. Folder confinement ---


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every data location at tmp_path/data (as a deployment would)."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OMNISYNC_DB_PATH", str(data / "omnisync.db"))
    monkeypatch.setenv("OMNISYNC_RCLONE_CONFIG", str(data / "rclone.conf"))
    monkeypatch.setenv("OMNISYNC_API_TOKEN_FILE", str(data / "api-token"))
    monkeypatch.setenv("OMNISYNC_VAPID_DIR", str(data / "vapid"))
    monkeypatch.delenv("OMNISYNC_BROWSE_ROOTS", raising=False)
    return data


def test_data_dirs_follow_the_configured_paths(data_dir):
    assert data_dir.resolve() in path_guard.data_dirs()


@pytest.mark.parametrize("rel", ["data", "data/", "data/sub", "data/vapid", "", "data/../data"])
def test_local_dir_in_or_around_the_data_dir_is_refused(data_dir, tmp_path, rel):
    with pytest.raises(ValidationError, match="data directory"):
        ProfileCreateRequest(**{**VALID_PROFILE, "local_dir": os.path.join(str(tmp_path), rel)})


def test_root_is_refused_and_siblings_are_fine(data_dir, tmp_path):
    with pytest.raises(ValidationError, match="data directory"):
        ProfileCreateRequest(**{**VALID_PROFILE, "local_dir": "/"})
    ok = str(tmp_path / "data-other")
    assert ProfileCreateRequest(**{**VALID_PROFILE, "local_dir": ok}).local_dir == ok


def test_symlink_into_the_data_dir_is_refused(data_dir, tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to(data_dir)
    with pytest.raises(ValidationError, match="data directory"):
        ProfileCreateRequest(**{**VALID_PROFILE, "local_dir": str(link / "x")})


def test_test_sync_and_local_backup_targets_refuse_the_data_dir(data_dir):
    with pytest.raises(ValidationError, match="data directory"):
        SyncTestRequest(local_dir=str(data_dir), remote_dir="gdrive:x")
    with pytest.raises(ValueError, match="data directory"):
        check_backup_target(BackupTargetType.LOCAL, str(data_dir / "backups"), None)
    # A remote target with the same text is a remote path, not a local one.
    check_backup_target(BackupTargetType.REMOTE, "gdrive:data", None)


async def test_profile_api_refuses_the_data_dir_with_a_clear_422(env, data_dir):  # noqa: F811
    resp = await env.client.post("/profiles", json={**VALID_PROFILE, "local_dir": str(data_dir)})
    assert resp.status_code == 422
    assert "data directory" in resp.text
    p = await env.create("Docs", env.folder("docs"))
    resp = await env.client.put(f"/profiles/{p['slug']}", json={"local_dir": str(data_dir / "x")})
    assert resp.status_code == 422
    assert "data directory" in resp.text


async def test_backup_target_api_refuses_the_data_dir(env, data_dir):  # noqa: F811
    await env.create("Docs", env.folder("docs"))
    resp = await env.client.post("/profiles/docs/backups", json={
        "name": "t", "target_path": str(data_dir / "b"), "target_type": "local",
    })
    assert resp.status_code == 422 and "data directory" in resp.text
    created = await env.client.post("/profiles/docs/backups", json={
        "name": "t", "target_path": env.folder("vault"), "target_type": "local",
    })
    assert created.status_code == 201
    resp = await env.client.put(
        f"/profiles/docs/backups/{created.json()['id']}", json={"target_path": str(data_dir)},
    )
    assert resp.status_code == 422 and "data directory" in resp.text


async def test_restore_into_a_stored_data_dir_profile_is_refused(env, data_dir):  # noqa: F811
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        profile = SyncProfile(
            slug="old", name="Old", local_dir=str(data_dir), remote_dir="gdrive:old",
            created_at=now, updated_at=now,
        )
        session.add(profile)
        await session.flush()
        target = BackupTarget(
            profile_id=profile.id, name="t", target_path="gdrive:backups", target_type="remote",
            retention_days=7, frequency_hours=24, backup_mode="mirror", enabled=True,
            created_at=now, updated_at=now,
        )
        session.add(target)
        await session.commit()
        target_id = target.id
    for scope in ("local_only", "both"):
        resp = await env.client.post(f"/profiles/old/backups/{target_id}/restore", json={
            "snapshot_id": "2026-01-01T00-00-00", "restore_scope": scope,
        })
        assert resp.status_code == 422, resp.text
    env_backup_service = __import__("backend.api.routes.backups", fromlist=["x"])._backup_service
    env_backup_service.restore.assert_not_called()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv("OMNISYNC_BROWSE_ROOTS", str(root))
    return root


async def test_new_profile_folders_must_be_inside_browse_roots(env, roots, tmp_path):  # noqa: F811
    outside = tmp_path / "outside"
    outside.mkdir()
    resp = await env.client.post("/profiles", json={**VALID_PROFILE, "local_dir": str(outside)})
    assert resp.status_code == 422 and "OMNISYNC_BROWSE_ROOTS" in resp.text
    inside = roots / "docs"
    inside.mkdir()
    assert (await env.client.post("/profiles", json={**VALID_PROFILE, "local_dir": str(inside)})).status_code == 201
    resp = await env.client.put("/profiles/docs", json={"local_dir": str(outside)})
    assert resp.status_code == 422


async def test_stored_profiles_outside_the_roots_keep_working(env, roots, tmp_path, caplog):  # noqa: F811
    outside = tmp_path / "legacy"
    outside.mkdir()
    now = datetime.now(timezone.utc)
    async with env.factory() as session:
        session.add(SyncProfile(
            slug="legacy", name="Legacy", local_dir=str(outside), remote_dir="gdrive:legacy",
            created_at=now, updated_at=now,
        ))
        await session.commit()
    # Saving it again with the same folder (as the forms do) is fine.
    resp = await env.client.put("/profiles/legacy", json={"local_dir": str(outside) + "/", "debounce_seconds": 9})
    assert resp.status_code == 200, resp.text
    with caplog.at_level(logging.WARNING, logger="backend.services.path_guard"):
        await path_guard.warn_about_stored_paths(env.factory)
    assert "legacy" in caplog.text and "OMNISYNC_BROWSE_ROOTS" in caplog.text


async def test_new_local_backup_targets_must_be_inside_browse_roots(env, roots, tmp_path):  # noqa: F811
    docs = roots / "docs"
    docs.mkdir()
    assert (await env.client.post("/profiles", json={**VALID_PROFILE, "local_dir": str(docs)})).status_code == 201
    resp = await env.client.post("/profiles/docs/backups", json={
        "name": "t", "target_path": str(tmp_path / "vault"), "target_type": "local",
    })
    assert resp.status_code == 422 and "OMNISYNC_BROWSE_ROOTS" in resp.text
    resp = await env.client.post("/profiles/docs/backups", json={
        "name": "r", "target_path": "gdrive:vault", "target_type": "remote",
    })
    assert resp.status_code == 201


async def test_test_sync_folder_must_be_inside_browse_roots(test_client, test_services, roots, tmp_path):
    test_services.rclone.test_sync = AsyncMock(return_value={"success": True, "steps": []})
    resp = await test_client.post("/config/test-sync", json={"local_dir": str(tmp_path), "remote_dir": "g:x"})
    assert resp.status_code == 422
    resp = await test_client.post("/config/test-sync", json={"local_dir": str(roots / "x"), "remote_dir": "g:x"})
    assert resp.status_code == 200
    test_services.rclone.test_sync.assert_awaited_once()


def test_unset_browse_roots_do_not_confine(monkeypatch):
    monkeypatch.delenv("OMNISYNC_BROWSE_ROOTS", raising=False)
    assert path_guard.check_in_browse_roots("/anywhere/at/all", "local_dir") == "/anywhere/at/all"


# --- 3. rclone_args ---


@pytest.mark.parametrize("args", [
    ["--fast-list", "x"],                  # a switch takes no separate value
    ["--checksum", "/etc"],
    ["--fast-list=yes"],
    ["--transfers"],                       # value missing at the end
    ["--transfers", "--fast-list"],        # value missing before the next flag
    ["--transfers", "-v"],
    ["--transfers="],
    ["--transfers", "4", "5"],
    ["--", "x"],
    ["-v"],
])
def test_misplaced_rclone_values_are_rejected(args):
    with pytest.raises(ValueError):
        check_rclone_args(args)


@pytest.mark.parametrize("args", [
    ["--fast-list", "--transfers", "4"],
    ["--fast-list=false", "--transfers=4"],
    ["--exclude", "*.tmp", "--checksum"],
    ["--max-age", "7d", "--drive-use-trash=true"],
    ["--max-delete", "-1"],
    ["--max-delete=-1"],
])
def test_well_formed_rclone_args_are_accepted(args):
    assert check_rclone_args(args) == args


_VALUE = st.text(st.characters(codec="utf-8", exclude_characters="\n\r\x00"), min_size=1, max_size=8)
_FLAG_TOKENS = st.one_of(
    st.sampled_from(sorted(SAFE_RCLONE_BOOL_FLAGS)).map(lambda f: [f"--{f}"]),
    st.tuples(st.sampled_from(sorted(SAFE_RCLONE_VALUE_FLAGS)), _VALUE.filter(lambda v: not v.startswith("-")))
    .map(lambda fv: [f"--{fv[0]}", fv[1]]),
    st.tuples(st.sampled_from(sorted(SAFE_RCLONE_VALUE_FLAGS)), _VALUE).map(lambda fv: [f"--{fv[0]}={fv[1]}"]),
)


@given(st.lists(_FLAG_TOKENS, max_size=6))
def test_property_well_formed_args_are_accepted(groups):
    args = [token for group in groups for token in group]
    assert check_rclone_args(args) == args


@given(st.lists(st.text(max_size=12), max_size=8))
def test_property_accepted_args_have_no_stray_positionals(args):
    """Whatever is accepted, every non-flag token is the value of the value flag right before it."""
    try:
        check_rclone_args(args)
    except ValueError:
        return
    previous = None
    for arg in args:
        if not arg.startswith("--"):
            assert previous is not None and previous[2:] in SAFE_RCLONE_VALUE_FLAGS
            previous = None
        else:
            name = arg[2:].partition("=")[0]
            assert name in SAFE_RCLONE_VALUE_FLAGS | SAFE_RCLONE_BOOL_FLAGS
            previous = arg if name in SAFE_RCLONE_VALUE_FLAGS and "=" not in arg else None
    assert previous is None  # no value flag left without its value


@given(st.sampled_from(sorted(SAFE_RCLONE_BOOL_FLAGS)), _VALUE.filter(lambda v: not v.startswith("-")))
def test_property_switch_followed_by_a_value_is_rejected(flag, value):
    with pytest.raises(ValueError):
        check_rclone_args([f"--{flag}", value])


# --- 4. Tokens and failed logins ---


def test_short_env_token_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("OMNISYNC_API_TOKEN", "short-token")
    monkeypatch.setattr(security, "_cached", None)
    with caplog.at_level(logging.WARNING, logger="backend.security"):
        assert security.get_api_token() == "short-token"
    assert "OMNISYNC_API_TOKEN is only 11 characters" in caplog.text


def test_long_env_token_is_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("OMNISYNC_API_TOKEN", "x" * 32)
    monkeypatch.setattr(security, "_cached", None)
    with caplog.at_level(logging.WARNING, logger="backend.security"):
        security.get_api_token()
    assert "characters" not in caplog.text


@pytest.fixture
async def bare_client(test_client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_failed_logins_are_logged_once_per_minute(bare_client, caplog):
    bad = {"Authorization": "Bearer wrong"}
    with caplog.at_level(logging.WARNING, logger="backend.audit"):
        for _ in range(3):
            assert (await bare_client.get("/logs", headers=bad)).status_code == 401
    lines = [r for r in caplog.records if r.getMessage().startswith("auth.token_rejected")]
    assert len(lines) == 1
    assert lines[0].name == "backend.audit"
    assert "client=127.0.0.1" in lines[0].getMessage() and "attempts=1" in lines[0].getMessage()
    assert "wrong" not in lines[0].getMessage()  # the guessed token is not logged


async def test_missing_token_is_not_counted(bare_client):
    for _ in range(security.AUTH_FAILURE_LIMIT + 2):
        assert (await bare_client.get("/logs")).status_code == 401
    assert (await bare_client.get("/logs", headers=AUTH_HEADERS)).status_code == 200


async def test_repeated_failures_get_429_then_recover(bare_client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(security.AUTH_FAILURE_LIMIT):
        assert (await bare_client.get("/logs", headers=bad)).status_code == 401
    blocked = await bare_client.get("/logs", headers=bad)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    # Without a token too; the health check stays reachable.
    assert (await bare_client.get("/logs")).status_code == 429
    assert (await bare_client.get("/health")).status_code != 429
    clock[0] += security.AUTH_BLOCK_SECONDS + 1
    assert (await bare_client.get("/logs", headers=bad)).status_code == 401


async def test_the_right_token_passes_a_blocked_address(bare_client, monkeypatch):
    """Failures from the owner's address (another local process, a spoofer)
    must not lock the owner out: the right token is never throttled."""
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(security.AUTH_FAILURE_LIMIT):
        await bare_client.get("/logs", headers=bad)
    assert (await bare_client.get("/logs", headers=bad)).status_code == 429
    assert (await bare_client.get("/logs", headers=AUTH_HEADERS)).status_code == 200
    # The block stays for everyone else.
    assert (await bare_client.get("/logs", headers=bad)).status_code == 429


async def test_forwarded_for_does_not_change_the_throttled_address(bare_client, monkeypatch):
    """The throttle keys on the TCP peer: a new X-Forwarded-For per request
    neither dodges the block nor blocks the address it names."""
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    for i in range(security.AUTH_FAILURE_LIMIT):
        headers = {"Authorization": "Bearer wrong", "X-Forwarded-For": f"203.0.113.{i}"}
        assert (await bare_client.get("/logs", headers=headers)).status_code == 401
    rotated = {"Authorization": "Bearer wrong", "X-Forwarded-For": "198.51.100.77"}
    assert (await bare_client.get("/logs", headers=rotated)).status_code == 429
    assert set(security._failures) == {"127.0.0.1"}


async def test_failures_spread_over_time_do_not_block(bare_client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(security.AUTH_FAILURE_LIMIT * 2):
        assert (await bare_client.get("/logs", headers=bad)).status_code == 401
        clock[0] += security.AUTH_FAILURE_WINDOW / (security.AUTH_FAILURE_LIMIT - 1)


def test_throttle_table_is_bounded(monkeypatch):
    monkeypatch.setattr(security, "_MAX_TRACKED_CLIENTS", 5)
    for i in range(50):
        security._record_failure(f"10.0.0.{i}", 1000.0)
    assert len(security._failures) <= 5


# --- 6. Size limits ---


def test_rclone_list_sizes_are_bounded():
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "rclone_args": ["--fast-list"] * (MAX_RCLONE_ARGS + 1)})
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "rclone_filter": ["- *.tmp"] * (MAX_RCLONE_FILTER_RULES + 1)})
    with pytest.raises(ValidationError):
        ProfileCreateRequest(**{**VALID_PROFILE, "rclone_filter": ["- " + "a" * 2000]})
    ok = ProfileCreateRequest(**{**VALID_PROFILE, "rclone_filter": ["- *.tmp"] * MAX_RCLONE_FILTER_RULES})
    assert len(ok.rclone_filter) == MAX_RCLONE_FILTER_RULES


def test_push_keys_are_bounded():
    endpoint = "https://fcm.googleapis.com/fcm/send/abc"
    ok = PushSubscriptionRequest(endpoint=endpoint, keys={"p256dh": "k" * 87, "auth": "a" * 22})
    assert set(ok.keys) == {"p256dh", "auth"}
    with pytest.raises(ValidationError):
        PushSubscriptionRequest(endpoint=endpoint, keys={f"k{i}": "v" for i in range(9)})
    with pytest.raises(ValidationError):
        PushSubscriptionRequest(endpoint=endpoint, keys={"p256dh": "k" * 513})


def test_other_request_lists_are_bounded():
    with pytest.raises(ValidationError):
        CreateRemoteRequest(name="n", provider_id="s3", params={f"k{i}": "v" for i in range(65)})
    with pytest.raises(ValidationError):
        SelectiveSyncRequest(items=[{"path": "a" * 5000, "action": "push"}])


async def test_oversized_body_is_413(test_client):
    resp = await test_client.post(
        "/profiles", content=b"{" + b" " * (security.MAX_BODY_BYTES + 1) + b"}",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


async def test_oversized_chunked_body_is_413(test_client):
    async def chunks():
        for _ in range(3):
            yield b" " * (security.MAX_BODY_BYTES // 2 + 1)

    resp = await test_client.post("/profiles", content=chunks(), headers={"content-type": "application/json"})
    assert resp.status_code == 413


async def test_body_under_the_limit_reaches_the_route(test_client):
    resp = await test_client.post("/profiles", json={"name": "x"})
    assert resp.status_code == 422  # validation, i.e. the body was read


async def test_conflict_listing_is_paginated(test_client, test_db_factory):
    async with test_db_factory() as session:
        for i in range(5):
            session.add(Conflict(file_path=f"f{i}.txt", resolved=False, profile_id=1))
        await session.commit()
    resp = await test_client.get("/conflicts")
    assert resp.status_code == 200
    assert len(resp.json()) == 5 and resp.headers["x-total-count"] == "5"
    page = await test_client.get("/conflicts", params={"skip": 1, "limit": 2})
    assert [c["file_path"] for c in page.json()] == ["f1.txt", "f2.txt"]
    assert page.headers["x-total-count"] == "5"
    assert (await test_client.get("/conflicts", params={"limit": 0})).status_code == 422
    assert (await test_client.get("/conflicts", params={"limit": 100000})).status_code == 422
