"""Managing existing remotes: edit, reconnect, new providers, import.

These run against a real rclone and a temporary rclone.conf: what is
checked is what ends up in the file and whether rclone can use it. Only
the OAuth token endpoint is faked (httpx.MockTransport); WebDAV and SMB
servers cannot be reached here, so for them the generated config is
checked with `rclone config show`.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import shutil
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.routes import remotes, wizard
from backend.exceptions import RcloneAuthError
from backend.main import app
from backend.services import oauth, remote_auth
from backend.services.provider_registry import (
    PROVIDERS,
    remote_capabilities,
    split_remote_path,
    validate_remote_params,
)
from backend.services.rclone import RcloneService
from backend.services.rclone_import import (
    ImportParseError,
    local_reference_problems,
    parse_rclone_config,
    referenced_remotes,
    rewrite_references,
)
from backend.services.wizard_sessions import WizardSessionManager
from backend.tests.auth import AUTH_HEADERS

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="needs the rclone binary")

CODE = "4/0AeanS0aExampleCode-_x"
NEW_TOKEN = "ya29.new-token-never-leaves-the-server"
OLD_TOKEN = json.dumps({"access_token": "ya29.old", "token_type": "Bearer", "refresh_token": "1//old",
                        "expiry": "2020-01-01T00:00:00Z"})


async def _rclone(*args: str) -> str:
    """Run rclone and return its output; fails the test if it fails."""
    proc = await asyncio.create_subprocess_exec(
        "rclone", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()
    return out.decode()


async def _reveal(value: str) -> str:
    return (await _rclone("reveal", "--", value)).strip()  # an obscured value may start with "-"


def _write_conf(path: Path, sections: dict[str, dict[str, str]]) -> None:
    config = configparser.RawConfigParser()
    config.optionxform = str  # type: ignore[assignment,method-assign]
    for name, options in sections.items():
        config[name] = options
    with path.open("w") as f:
        config.write(f)


def _read_conf(path: Path) -> configparser.RawConfigParser:
    config = configparser.RawConfigParser()
    config.optionxform = str  # type: ignore[assignment,method-assign]
    config.read(path)
    return config


@pytest.fixture
def conf(tmp_path) -> Path:
    return tmp_path / "rclone.conf"


@pytest.fixture
async def client(monkeypatch, conf):
    monkeypatch.delenv("OMNISYNC_OAUTH_REDIRECT_URI", raising=False)
    rclone = RcloneService(rclone_config_path=str(conf))
    monkeypatch.setattr(wizard, "_rclone_service", rclone)
    monkeypatch.setattr(wizard, "_session_manager", WizardSessionManager())
    monkeypatch.setattr(remotes, "_rclone_service", rclone)
    monkeypatch.setattr(remotes, "_db_factory", None)
    remote_auth.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c
    remote_auth.reset()


@pytest.fixture
def token_endpoint(monkeypatch) -> list[dict[str, str]]:
    sent: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={
            "access_token": NEW_TOKEN, "token_type": "Bearer", "expires_in": 3599, "refresh_token": "1//new",
        })

    monkeypatch.setattr(oauth, "_http_transport", httpx.MockTransport(answer))
    return sent


# --- Registry: capabilities and validation ---


class TestRegistry:
    def test_capabilities(self) -> None:
        assert remote_capabilities("drive").reconnectable and not remote_capabilities("drive").editable
        crypt = remote_capabilities("crypt")
        assert crypt.editable and not crypt.reconnectable and crypt.provider_id == "crypt"
        unknown = remote_capabilities("pcloud")
        assert unknown.provider_id is None and not unknown.editable and not unknown.reconnectable

    @pytest.mark.parametrize("value", ["/data/omnisync", ":local:/data", "-x:path", "", "noc0lon"])
    def test_remote_path_must_name_a_remote(self, value: str) -> None:
        assert split_remote_path(value) is None

    def test_select_and_port_and_remote_path_are_checked(self) -> None:
        errors = validate_remote_params(PROVIDERS["crypt"], {
            "remote": "gone:x", "filename_encryption": "rot13",
        }, has_token=False, existing_remotes={"base"}, own_name="secret")
        assert any("no remote named 'gone'" in e for e in errors)
        assert any("filename_encryption" in e for e in errors)
        assert validate_remote_params(PROVIDERS["crypt"], {"remote": "secret:x"}, has_token=False,
                                      existing_remotes={"secret"}, own_name="secret")
        assert validate_remote_params(PROVIDERS["smb"], {"port": "99999"}, has_token=False)
        assert not validate_remote_params(PROVIDERS["smb"], {"port": "445"}, has_token=False)

    def test_default_is_not_a_remote_name(self) -> None:
        from backend.services.provider_registry import validate_remote_name
        assert not validate_remote_name("DEFAULT")


# --- New providers ---


class TestNewProviders:
    async def test_webdav_config_is_generated_and_password_obscured(self, client, conf) -> None:
        resp = await client.post("/wizard/create", json={"name": "cloud", "provider_id": "webdav", "params": {
            "url": "https://cloud.example.com/remote.php/dav/files/me/", "vendor": "nextcloud",
            "user": "me", "pass": "s3cret pass",
        }})
        assert resp.status_code == 200, resp.text
        section = _read_conf(conf)["cloud"]
        assert section["type"] == "webdav" and section["vendor"] == "nextcloud"
        assert section["pass"] != "s3cret pass" and await _reveal(section["pass"]) == "s3cret pass"
        shown = await _rclone("--config", str(conf), "config", "show", "cloud")
        assert "type = webdav" in shown

    async def test_webdav_vendor_must_be_an_option(self, client) -> None:
        resp = await client.post("/wizard/create", json={"name": "cloud", "provider_id": "webdav", "params": {
            "url": "https://x", "vendor": "evilcorp",
        }})
        assert resp.status_code == 422
        assert any("vendor" in e for e in resp.json()["details"]["errors"])

    async def test_smb_config(self, client, conf) -> None:
        resp = await client.post("/wizard/create", json={"name": "nas", "provider_id": "smb", "params": {
            "host": "nas.local", "port": "445", "user": "me", "pass": "pw", "domain": "HOME",
        }})
        assert resp.status_code == 200, resp.text
        section = _read_conf(conf)["nas"]
        assert (section["type"], section["host"], section["domain"]) == ("smb", "nas.local", "HOME")
        assert await _reveal(section["pass"]) == "pw"

    async def test_required_fields_are_enforced(self, client) -> None:
        resp = await client.post("/wizard/create", json={"name": "nas", "provider_id": "smb", "params": {}})
        assert resp.status_code == 422
        assert "'host' is required" in resp.json()["details"]["errors"]

    async def test_crypt_over_a_local_alias_encrypts(self, client, conf, tmp_path) -> None:
        store = tmp_path / "store"
        store.mkdir()
        _write_conf(conf, {"base": {"type": "alias", "remote": str(store)}})
        resp = await client.post("/wizard/create", json={"name": "secret", "provider_id": "crypt", "params": {
            "remote": "base:vault", "password": "correct horse", "password2": "battery staple",
            "filename_encryption": "standard", "directory_name_encryption": "true",
        }})
        assert resp.status_code == 200, resp.text
        section = _read_conf(conf)["secret"]
        assert section["remote"] == "base:vault"
        assert await _reveal(section["password"]) == "correct horse"

        src = tmp_path / "plain"
        (src / "Docs").mkdir(parents=True)
        (src / "Docs" / "hello.txt").write_text("hello world")
        await _rclone("--config", str(conf), "copy", "--", str(src), "secret:")
        listed = await _rclone("--config", str(conf), "lsd", "--", "secret:")
        assert "Docs" in listed
        stored = [p.name for p in (store / "vault").rglob("*")]
        assert stored and "Docs" not in stored and "hello.txt" not in stored
        assert "hello world" not in "".join(p.read_bytes().decode("latin-1") for p in (store / "vault").rglob("*")
                                            if p.is_file())

    @pytest.mark.parametrize("target, message", [
        ("missing:x", "no remote named"),
        ("/etc", "existing remote"),
        (":local:/etc", "existing remote"),
        ("secret:loop", "itself"),
    ])
    async def test_crypt_target_must_be_another_existing_remote(self, client, conf, target, message) -> None:
        _write_conf(conf, {"base": {"type": "alias", "remote": "/tmp"}})
        resp = await client.post("/wizard/create", json={"name": "secret", "provider_id": "crypt", "params": {
            "remote": target, "password": "pw",
        }})
        assert resp.status_code == 422
        assert any(message in e for e in resp.json()["details"]["errors"])


# --- Edit ---


class TestEditRemote:
    @pytest.fixture
    async def sftp(self, client, conf) -> None:
        resp = await client.post("/wizard/create", json={"name": "box", "provider_id": "sftp", "params": {
            "host": "box.example", "user": "me", "pass": "old-password", "port": "22",
        }})
        assert resp.status_code == 200, resp.text
        cfg = _read_conf(conf)
        cfg.set("box", "md5sum_command", "md5sum")  # not a wizard field: must survive edits
        with conf.open("w") as f:
            cfg.write(f)

    async def test_get_config_masks_secrets(self, client, conf, sftp) -> None:
        stored = _read_conf(conf)["box"]["pass"]
        resp = await client.get("/remotes/box/config")
        assert resp.status_code == 200
        body = resp.json()
        fields = {f["name"]: f for f in body["fields"]}
        assert fields["host"] == {"name": "host", "value": "box.example", "is_set": True, "secret": False}
        assert fields["pass"] == {"name": "pass", "value": "", "is_set": True, "secret": True}
        assert fields["key_file"]["is_set"] is False
        assert body["other_keys"] == ["md5sum_command"]
        assert stored not in resp.text and "old-password" not in resp.text

    async def test_update_keeps_empty_secret_and_other_options(self, client, conf, sftp) -> None:
        before = _read_conf(conf)["box"]["pass"]
        resp = await client.put("/remotes/box", json={"params": {"host": "new.example", "pass": "", "port": ""}})
        assert resp.status_code == 200, resp.text
        section = _read_conf(conf)["box"]
        assert section["host"] == "new.example" and section["pass"] == before
        assert "port" not in section and section["md5sum_command"] == "md5sum"
        assert section["type"] == "sftp"

    async def test_update_replaces_and_clears_secrets(self, client, conf, sftp) -> None:
        resp = await client.put("/remotes/box", json={"params": {"pass": "new-password"}})
        assert resp.status_code == 200
        assert await _reveal(_read_conf(conf)["box"]["pass"]) == "new-password"
        resp = await client.put("/remotes/box", json={"params": {"key_file": "/keys/id"}, "clear": ["pass"]})
        assert resp.status_code == 200
        section = _read_conf(conf)["box"]
        assert "pass" not in section and section["key_file"] == "/keys/id"

    async def test_update_logs_option_names_not_values(self, client, conf, sftp, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="backend.services.rclone"):
            resp = await client.put("/remotes/box", json={"params": {"host": "h2.example", "pass": "s3cret-pw"}})
        assert resp.status_code == 200
        assert "Updated remote 'box' (changed: host, pass; removed: -)" in caplog.text
        obscured = _read_conf(conf)["box"]["pass"]
        assert "s3cret-pw" not in caplog.text and obscured not in caplog.text
        assert "h2.example" not in caplog.text

    @pytest.mark.parametrize("body", [
        {"params": {"ssh": "touch /tmp/pwned"}},
        {"params": {"type": "local"}},
        {"params": {"host": "a\nssh = x"}},
        {"params": {"host": ""}},
        {"clear": ["host"]},
        {"params": {"pass": "x"}, "clear": ["pass"]},
    ])
    async def test_update_is_validated(self, client, conf, sftp, body) -> None:
        before = conf.read_text()
        resp = await client.put("/remotes/box", json=body)
        assert resp.status_code == 422
        assert conf.read_text() == before

    async def test_unknown_field_in_request_body_is_rejected(self, client, sftp) -> None:
        resp = await client.put("/remotes/box", json={"params": {}, "token": "x"})
        assert resp.status_code == 422

    async def test_oauth_and_foreign_types_are_not_edited(self, client, conf) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}, "pc": {"type": "pcloud"}})
        resp = await client.put("/remotes/gdrive", json={"params": {"client_id": "x"}})
        assert resp.status_code == 409 and "reconnect" in resp.json()["detail"]
        cfg = await client.get("/remotes/gdrive/config")
        assert cfg.status_code == 200 and "ya29" not in cfg.text and cfg.json()["other_keys"] == ["token"]
        assert (await client.get("/remotes/pc/config")).status_code == 409
        assert (await client.put("/remotes/missing", json={"params": {}})).status_code == 404
        assert (await client.get("/remotes/-bad/config")).status_code == 422

    async def test_list_reports_capabilities(self, client, conf) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}, "box": {"type": "sftp", "host": "h"}})
        remote_auth.mark_auth_failed("gdrive")
        listed = {r["name"]: r for r in (await client.get("/remotes")).json()}
        assert listed["gdrive"]["reconnectable"] and not listed["gdrive"]["editable"]
        assert listed["gdrive"]["auth_error"] is True
        assert listed["box"]["editable"] and listed["box"]["provider_id"] == "sftp"
        assert listed["box"]["auth_error"] is False


# --- Reconnect ---


class TestReconnect:
    @pytest.fixture
    def gdrive(self, conf) -> None:
        _write_conf(conf, {
            "gdrive": {"type": "drive", "client_id": "own.apps.googleusercontent.com",
                       "client_secret": "own-secret", "root_folder_id": "abc", "token": OLD_TOKEN},
            "secret": {"type": "crypt", "remote": "gdrive:x", "password": "y"},
        })

    async def _complete(self, client, **body) -> str:
        resp = await client.post("/wizard/authorize", json={"provider_id": "drive", "remote_name": "gdrive", **body})
        assert resp.status_code == 200, resp.text
        sid = resp.json()["session_id"]
        session = wizard._session_manager.get_session(sid)
        assert session is not None
        # The callback flow (own app): finish as the provider's redirect would.
        done = await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert done.status_code == 200, done.text
        return sid

    async def test_reconnect_replaces_only_the_token(self, client, conf, gdrive, token_endpoint) -> None:
        remote_auth.mark_auth_failed("gdrive")
        sid = await self._complete(client)
        # The remote's own app signed in, not rclone's built-in one.
        assert token_endpoint[0]["client_id"] == "own.apps.googleusercontent.com"
        assert token_endpoint[0]["client_secret"] == "own-secret"

        resp = await client.post("/wizard/reconnect", json={"name": "gdrive", "session_id": sid})
        assert resp.status_code == 200, resp.text
        assert NEW_TOKEN not in resp.text
        section = _read_conf(conf)["gdrive"]
        assert json.loads(section["token"])["access_token"] == NEW_TOKEN
        assert section["client_id"] == "own.apps.googleusercontent.com"
        assert section["root_folder_id"] == "abc" and section["client_secret"] == "own-secret"
        assert not remote_auth.auth_failed("gdrive")
        # The session is used up.
        again = await client.post("/wizard/reconnect", json={"name": "gdrive", "session_id": sid})
        assert again.status_code == 404

    async def test_reconnect_with_new_app_credentials_stores_them(self, client, conf, gdrive, token_endpoint) -> None:
        sid = await self._complete(client, client_id="other.apps", client_secret="other-secret")
        assert token_endpoint[0]["client_id"] == "other.apps"
        assert (await client.post("/wizard/reconnect", json={"name": "gdrive", "session_id": sid})).status_code == 200
        section = _read_conf(conf)["gdrive"]
        assert section["client_id"] == "other.apps" and section["client_secret"] == "other-secret"

    async def test_new_public_client_drops_the_old_secret(self, client, conf, token_endpoint) -> None:
        _write_conf(conf, {"dbx": {"type": "dropbox", "client_id": "old-key", "client_secret": "old",
                                   "token": OLD_TOKEN}})
        resp = await client.post("/wizard/authorize", json={
            "provider_id": "dropbox", "remote_name": "dbx", "client_id": "new-key",
        })
        assert resp.status_code == 200, resp.text
        sid = resp.json()["session_id"]
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert "client_secret" not in token_endpoint[0]
        assert (await client.post("/wizard/reconnect", json={"name": "dbx", "session_id": sid})).status_code == 200
        section = _read_conf(conf)["dbx"]
        assert section["client_id"] == "new-key" and "client_secret" not in section

    async def test_builtin_app_remote_needs_an_own_app(self, client, conf, token_endpoint) -> None:
        """A remote made with rclone's built-in app has no client_id: a
        reconnect needs the user's own app, and then stores it."""
        _write_conf(conf, {"dbx": {"type": "dropbox", "token": OLD_TOKEN}})
        refused = await client.post("/wizard/authorize", json={"provider_id": "dropbox", "remote_name": "dbx"})
        assert refused.status_code == 422
        assert refused.json()["code"] == "oauth_client_id_required"
        assert not wizard._session_manager._sessions  # no session was opened

        resp = await client.post("/wizard/authorize", json={
            "provider_id": "dropbox", "remote_name": "dbx", "client_id": "own-key",
        })
        assert resp.status_code == 200, resp.text
        sid = resp.json()["session_id"]
        assert resp.json()["redirect_uri"] == "http://test/wizard/oauth/callback"
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": sid})
        assert token_endpoint[0]["client_id"] == "own-key"
        assert (await client.post("/wizard/reconnect", json={"name": "dbx", "session_id": sid})).status_code == 200
        section = _read_conf(conf)["dbx"]
        assert json.loads(section["token"])["access_token"] == NEW_TOKEN
        assert section["client_id"] == "own-key"

    async def test_google_remote_without_secret_needs_one(self, client, conf, token_endpoint) -> None:
        _write_conf(conf, {"gd": {"type": "drive", "client_id": "own.apps", "token": OLD_TOKEN}})
        resp = await client.post("/wizard/authorize", json={"provider_id": "drive", "remote_name": "gd"})
        assert resp.status_code == 422 and resp.json()["code"] == "oauth_client_secret_required"
        assert token_endpoint == []

    @pytest.mark.parametrize("body, status", [
        ({"provider_id": "crypt", "remote_name": "secret"}, 422),
        ({"provider_id": "dropbox", "remote_name": "gdrive"}, 422),
        ({"provider_id": "drive", "remote_name": "secret"}, 422),
        ({"provider_id": "drive", "remote_name": "missing"}, 404),
        ({"provider_id": "drive", "remote_name": "--config=/x"}, 422),
    ])
    async def test_only_oauth_remotes_of_the_provider_reconnect(self, client, gdrive, body, status) -> None:
        resp = await client.post("/wizard/authorize", json=body)
        assert resp.status_code == status, resp.text

    async def test_sessions_cannot_be_mixed_up(self, client, conf, gdrive, token_endpoint) -> None:
        sid = await self._complete(client)
        # A reconnect session cannot create a new remote ...
        create = await client.post("/wizard/create", json={
            "name": "copy", "provider_id": "drive", "params": {}, "session_id": sid,
        })
        assert create.status_code == 422
        # ... nor reconnect another remote.
        other = await client.post("/wizard/reconnect", json={"name": "secret", "session_id": sid})
        assert other.status_code == 422
        # A new-remote session cannot reconnect.
        new = await client.post("/wizard/authorize", json={
            "provider_id": "drive", "client_id": "x.apps", "client_secret": "x-secret",
        })
        new_sid = new.json()["session_id"]
        await client.get("/wizard/oauth/callback", params={"code": CODE, "state": new_sid})
        resp = await client.post("/wizard/reconnect", json={"name": "gdrive", "session_id": new_sid})
        assert resp.status_code == 422
        assert json.loads(_read_conf(conf)["gdrive"]["token"])["access_token"] == "ya29.old"


# --- Auth failures ---


class TestAuthErrorDetection:
    async def test_connection_test_marks_and_clears(self, client, conf, monkeypatch) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}})
        rclone = remotes._rclone_service
        real_run = rclone._run
        refuse = True

        async def run(args, **kw):
            if args[0] == "lsd":
                if refuse:
                    raise RcloneAuthError("rclone authentication error: x")
                return None
            return await real_run(args, **kw)

        monkeypatch.setattr(rclone, "_run", run)
        resp = await client.post("/remotes/gdrive/test")
        assert resp.json()["success"] is False and resp.json()["auth_error"] is True
        assert (await client.get("/remotes")).json()[0]["auth_error"] is True

        refuse = False
        assert (await client.post("/remotes/gdrive/test")).json()["auth_error"] is False
        assert not remote_auth.auth_failed("gdrive")

    async def test_rejected_token_in_api_check_marks_remote(self, conf, monkeypatch) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "client_id": "own.apps",
                                      "token": json.dumps({"access_token": "a"})}})
        remote_auth.reset()

        class Refused(httpx.AsyncClient):
            def __init__(self, **kw):
                super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})), **kw)

        monkeypatch.setattr("backend.services.rclone.auth.httpx.AsyncClient", Refused)
        assert await RcloneService(rclone_config_path=str(conf)).check_remote("gdrive") is False
        assert remote_auth.auth_failed("gdrive")
        remote_auth.reset()


    def test_sync_auth_failure_marks_the_profiles_remote(self) -> None:
        from types import SimpleNamespace

        from backend.services.sync_engine import SyncEngine

        remote_auth.reset()
        engine = SimpleNamespace(_profile=SimpleNamespace(remote_dir="gdrive:Docs"))
        SyncEngine._note_remote_auth(engine, "sync_failed", "network is unreachable")  # type: ignore[arg-type]
        assert not remote_auth.auth_failed("gdrive")
        SyncEngine._note_remote_auth(engine, "auth_error", "rclone authentication error: invalid_grant")  # type: ignore[arg-type]
        assert remote_auth.auth_failed("gdrive")
        SyncEngine._note_remote_auth(engine, "sync_completed", "")  # type: ignore[arg-type]
        assert not remote_auth.auth_failed("gdrive")


# --- Import ---


CONF_TEXT = """\
# exported from another machine
[gdrive]
type = drive
client_id = abc
token = {"access_token":"ya29.imported","expiry":"2026-01-01T00:00:00Z"}

[vault]
type = crypt
remote = gdrive:Vault
password = OBSCURED_VALUE

[evil]
type = sftp
host = h
ssh = sh -c 'touch /tmp/pwned'

[disk]
type = local

[lp]
type = alias
remote = /data/omnisync

[-flag]
type = memory
"""


class TestImportParser:
    def test_sections_and_problems(self) -> None:
        sections = {s.name: s for s in parse_rclone_config(CONF_TEXT)}
        assert sections["gdrive"].importable and sections["vault"].importable
        assert sections["gdrive"].options["token"].startswith('{"access_token":"ya29.imported"')
        assert next(iter(sections["vault"].options)) == "type"
        assert any("ssh" in p for p in sections["evil"].problems)
        assert any("local remotes" in p for p in sections["disk"].problems)
        assert any("local path" in p for p in sections["lp"].problems)
        assert any("valid remote name" in p for p in sections["-flag"].problems)

    @pytest.mark.parametrize("text, message", [
        ("RCLONE_ENCRYPT_V0:\nabcdef", "encrypted"),
        ("type = drive\n", "does not start"),
        ("[a]\ntype = drive\n[a]\ntype = s3\n", "appears twice"),
        ("[a]\ntype = drive\ntype = s3\n", "twice"),
    ])
    def test_unreadable_files(self, text: str, message: str) -> None:
        with pytest.raises(ImportParseError, match=message):
            parse_rclone_config(text)

    def test_parse_error_does_not_quote_the_line(self) -> None:
        with pytest.raises(ImportParseError) as e:
            parse_rclone_config("[a]\ntype = s3\n  secret_access_key_continued\n[b\n")
        assert "secret" not in str(e.value)

    def test_multiline_values_and_bearer_command_are_refused(self) -> None:
        sections = {s.name: s for s in parse_rclone_config(
            "[w]\ntype = webdav\nbearer_token_command = curl x\n[m]\ntype = s3\nkey = a\n  b\n"
            "[u]\ntype = union\nupstreams = a:x b:y:ro :local:/etc\n[s]\ntype = sftp\nmd5sum_command = md5sum\n"
        )}
        assert any("bearer_token_command" in p for p in sections["w"].problems)
        assert any("several lines" in p for p in sections["m"].problems)
        assert any("local path" in p for p in sections["u"].problems)
        assert sections["s"].importable  # runs on the server, not here

    def test_rewrite_references(self) -> None:
        assert rewrite_references({"type": "crypt", "remote": "gdrive:V"}, {"gdrive": "gdrive2"})["remote"] == "gdrive2:V"
        out = rewrite_references({"type": "union", "upstreams": "a:x b:y:ro"}, {"a": "c"})
        assert out["upstreams"] == "c:x b:y:ro"
        out = rewrite_references({"type": "combine", "upstreams": "dir=a:x"}, {"a": "c"})
        assert out["upstreams"] == "dir=c:x"


class TestImportLocalReach:
    """Options that make rclone read a local file, and wrappers of local remotes."""

    @pytest.fixture
    def data_dir(self, monkeypatch, tmp_path) -> Path:
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setenv("OMNISYNC_DB_PATH", str(data / "omnisync.db"))
        return data

    def _problems(self, text: str) -> list[str]:
        (section,) = parse_rclone_config(text)
        return section.problems

    @pytest.mark.parametrize("key", [
        "key_file", "pubkey_file", "known_hosts_file", "service_account_file",
        "global.ca_cert", "override.client_key", "client_cert",
    ])
    def test_file_option_into_the_data_dir_is_refused(self, data_dir, key) -> None:
        problems = self._problems(f"[s]\ntype = sftp\nhost = h\n{key} = {data_dir}/rclone.conf\n")
        assert any(key in p and "data directory" in p for p in problems)
        assert str(data_dir) not in " ".join(problems)  # the value is never echoed

    def test_file_option_through_a_symlink_or_dots_is_refused(self, data_dir, tmp_path) -> None:
        link = tmp_path / "innocent"
        link.symlink_to(data_dir)
        assert self._problems(f"[s]\ntype = sftp\nkey_file = {link}/api-token\n")
        assert self._problems(f"[s]\ntype = sftp\nknown_hosts_file = {tmp_path}/x/../data/omnisync.db\n")

    def test_file_option_with_an_environment_variable_is_refused(self, data_dir) -> None:
        problems = self._problems("[s]\ntype = sftp\nkey_file = ${RCLONE_CONFIG_DIR}/rclone.conf\n")
        assert any("environment variable" in p for p in problems)

    def test_file_option_elsewhere_is_imported(self, data_dir, tmp_path) -> None:
        assert self._problems(
            f"[s]\ntype = sftp\nhost = h\nkey_file = {tmp_path}/id_ed25519\nknown_hosts_file = ~/.ssh/known_hosts\n"
            "key_file_pass = OBSC\n"
        ) == []

    def test_referenced_remotes(self) -> None:
        assert referenced_remotes({"type": "crypt", "remote": "a:x"}) == ["a"]
        assert referenced_remotes({"type": "union", "upstreams": "a:x b:y:ro a:z"}) == ["a", "b"]
        assert referenced_remotes({"type": "combine", "upstreams": "dir=c:x"}) == ["c"]
        assert referenced_remotes({"type": "drive"}) == []

    async def test_local_reference_chain(self) -> None:
        existing = {
            "disk": {"type": "local"},
            "via_alias": {"type": "alias", "remote": "disk:sub"},
            "wraps_path": {"type": "crypt", "remote": "/srv/backups"},
            "cloud": {"type": "drive"},
            "loop_a": {"type": "alias", "remote": "loop_b:"},
            "loop_b": {"type": "alias", "remote": "loop_a:"},
        }

        async def lookup(name: str):
            return existing.get(name)

        async def problems(options: dict[str, str]) -> list[str]:
            return await local_reference_problems(options, lookup)

        assert "local remote" in (await problems({"type": "crypt", "remote": "disk:x"}))[0]
        assert "'disk'" in (await problems({"type": "crypt", "remote": "via_alias:x"}))[0]
        assert "wraps a local path" in (await problems({"type": "alias", "remote": "wraps_path:"}))[0]
        assert await problems({"type": "union", "upstreams": "cloud:a disk:b"})
        assert await problems({"type": "crypt", "remote": "cloud:x"}) == []
        assert await problems({"type": "crypt", "remote": "missing:x"}) == []
        assert await problems({"type": "alias", "remote": "loop_a:"}) == []  # a cycle ends


class TestImportRoutes:
    async def test_preview_lists_without_values(self, client, conf, caplog) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}})
        resp = await client.post("/remotes/import/preview", json={"content": CONF_TEXT})
        assert resp.status_code == 200
        found = {r["name"]: r for r in resp.json()["remotes"]}
        assert found["gdrive"]["exists"] is True and found["vault"]["exists"] is False
        assert found["vault"]["keys"] == ["remote", "password"]
        assert found["evil"]["problems"]
        for secret in ("ya29.imported", "OBSCURED_VALUE", "touch /tmp/pwned"):
            assert secret not in resp.text
            assert secret not in caplog.text

    async def test_preview_of_garbage_reports_an_error(self, client) -> None:
        resp = await client.post("/remotes/import/preview", json={"content": "hello"})
        assert resp.status_code == 200 and resp.json()["errors"]

    async def test_import_with_rename_follows_references(self, client, conf, caplog) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}})
        resp = await client.post("/remotes/import", json={"content": CONF_TEXT, "remotes": [
            {"source": "gdrive", "name": "gdrive2"}, {"source": "vault"},
        ]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["imported"] == ["gdrive2", "vault"]
        cfg = _read_conf(conf)
        assert cfg["gdrive"]["token"] == OLD_TOKEN  # the existing one is untouched
        assert cfg["gdrive2"]["token"].startswith('{"access_token":"ya29.imported"')
        assert cfg["vault"]["remote"] == "gdrive2:Vault" and cfg["vault"]["password"] == "OBSCURED_VALUE"
        assert "ya29.imported" not in caplog.text and "OBSCURED_VALUE" not in caplog.text
        await _rclone("--config", str(conf), "config", "show", "vault")

    async def test_clash_is_409_and_nothing_is_written(self, client, conf) -> None:
        _write_conf(conf, {"gdrive": {"type": "drive", "token": OLD_TOKEN}})
        before = conf.read_text()
        resp = await client.post("/remotes/import", json={"content": CONF_TEXT, "remotes": [
            {"source": "vault"}, {"source": "gdrive"},
        ]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "name_clash" and resp.json()["details"] == {"names": ["gdrive"]}
        assert "gdrive" in resp.json()["detail"]
        assert conf.read_text() == before

    @pytest.mark.parametrize("remotes_sel", [
        [{"source": "evil"}],
        [{"source": "disk"}],
        [{"source": "lp"}],
        [{"source": "nope"}],
        [{"source": "vault", "name": "-x"}],
        [{"source": "vault", "name": "DEFAULT"}],
        # reserved for encrypted backup targets (case and '-' do not matter to rclone's lookup)
        [{"source": "vault", "name": "omnisync_backup_crypt_1"}],
        [{"source": "vault", "name": "OmniSync-Backup-Crypt-x"}],
        # reserved for two-way syncs of long paths
        [{"source": "vault", "name": "omnisync_bisync_1_remote"}],
        [{"source": "vault", "name": "OmniSync-Bisync-x"}],
        [{"source": "vault", "name": "a"}, {"source": "gdrive", "name": "a"}],
    ])
    async def test_refused_selections(self, client, conf, remotes_sel) -> None:
        resp = await client.post("/remotes/import", json={"content": CONF_TEXT, "remotes": remotes_sel})
        assert resp.status_code == 422
        assert not conf.exists()

    async def test_a_reserved_section_name_is_refused(self, client, conf) -> None:
        text = "[omnisync_backup_crypt_3]\ntype = crypt\nremote = gdrive:V\npassword = OBSC\n"
        resp = await client.post("/remotes/import/preview", json={"content": text})
        assert resp.status_code == 200
        assert any("reserved" in p for p in resp.json()["remotes"][0]["problems"])
        resp = await client.post("/remotes/import", json={"content": text, "remotes": [
            {"source": "omnisync_backup_crypt_3"},
        ]})
        assert resp.status_code == 422 and "reserved" in resp.text
        assert not conf.exists()

    @pytest.mark.parametrize("existing, wrapper", [
        ({"disk": {"type": "local"}}, "[w]\ntype = crypt\nremote = disk:secret\npassword = OBSC\n"),
        ({"disk": {"type": "local"}, "a": {"type": "alias", "remote": "disk:x"}},
         "[w]\ntype = alias\nremote = a:\n"),
        ({"omnisync_backup_crypt_1": {"type": "crypt", "remote": "/backups", "password": "OBSC"}},
         "[w]\ntype = alias\nremote = omnisync_backup_crypt_1:\n"),
        ({"disk": {"type": "local"}, "cloud": {"type": "memory"}},
         "[w]\ntype = union\nupstreams = cloud:a disk:b\n"),
    ])
    async def test_wrapper_of_an_existing_local_remote_is_refused(self, client, conf, existing, wrapper) -> None:
        _write_conf(conf, existing)
        before = conf.read_text()
        resp = await client.post("/remotes/import/preview", json={"content": wrapper})
        assert resp.status_code == 200
        (candidate,) = resp.json()["remotes"]
        assert candidate["problems"] and "local" in candidate["problems"][0]
        resp = await client.post("/remotes/import", json={"content": wrapper, "remotes": [{"source": "w"}]})
        assert resp.status_code == 422, resp.text
        assert "local" in resp.text
        assert conf.read_text() == before

    async def test_wrapper_of_a_local_section_in_the_same_file_is_flagged(self, client, conf) -> None:
        resp = await client.post("/remotes/import/preview", json={
            "content": "[disk]\ntype = local\n[w]\ntype = crypt\nremote = disk:x\npassword = OBSC\n",
        })
        found = {r["name"]: r for r in resp.json()["remotes"]}
        assert any("'disk', a local remote" in p for p in found["w"]["problems"])

    async def test_wrapper_of_an_imported_cloud_remote_still_works(self, client, conf) -> None:
        """A rename onto a name the existing config uses for a local remote is a
        clash, never a silent re-point at it."""
        _write_conf(conf, {"disk": {"type": "local"}})
        text = "[disk]\ntype = memory\n[w]\ntype = alias\nremote = disk:x\n"
        resp = await client.post("/remotes/import", json={"content": text, "remotes": [
            {"source": "disk", "name": "mem"}, {"source": "w"},
        ]})
        assert resp.status_code == 200, resp.text
        assert _read_conf(conf)["w"]["remote"] == "mem:x"

    async def test_key_file_into_the_data_dir_is_refused_by_the_route(self, client, conf, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("OMNISYNC_DB_PATH", str(tmp_path / "omnisync.db"))
        text = f"[s]\ntype = sftp\nhost = h\nkey_file = {tmp_path}/api-token\n"
        resp = await client.post("/remotes/import", json={"content": text, "remotes": [{"source": "s"}]})
        assert resp.status_code == 422 and "key_file" in resp.text
        assert str(tmp_path) not in resp.text
        assert not conf.exists()

    async def test_size_limit(self, client) -> None:
        big = "[a]\ntype = memory\n" + "# x\n" * 200_000
        assert (await client.post("/remotes/import/preview", json={"content": big})).status_code == 422
