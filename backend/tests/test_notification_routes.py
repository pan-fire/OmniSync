"""Tests for notification API routes."""

from __future__ import annotations

import json

import toml
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.tests.auth import AUTH_HEADERS
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import notifications
from backend.db.database import get_session
from backend.db.models import Base, NotificationLog
from backend.main import app
from backend.services.config import ConfigService
from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_dispatcher import NotificationDispatcher


@pytest_asyncio.fixture
async def notif_client(tmp_path):
    """httpx AsyncClient with notification system mocked."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    # Create a mock dispatcher
    mock_dispatcher = MagicMock()
    mock_dispatcher.get_config.return_value = {
        "webpush": {"enabled": True, "min_severity": "warning"},
        "host_native": {"enabled": False, "min_severity": "warning"},
    }
    mock_dispatcher.get_channel_status = AsyncMock(return_value={
        "webpush": {"available": True},
        "host_native": {"available": False},
    })
    mock_dispatcher.dispatch = AsyncMock(return_value=(["webpush"], {}))
    mock_dispatcher._config_service = MagicMock()
    mock_dispatcher._config_service._load_toml.return_value = {}
    mock_dispatcher.reload = MagicMock()
    mock_dispatcher.channel_names = ["webpush", "host_native"]
    mock_dispatcher.config_error = False
    mock_dispatcher.unknown_channels.return_value = []

    # Create a mock webpush channel
    mock_webpush = MagicMock()
    mock_webpush.get_public_key.return_value = "BLtest_vapid_public_key_data"
    mock_webpush.add_subscription = AsyncMock()
    mock_webpush.remove_subscription = AsyncMock(return_value=True)

    notifications.set_dispatcher(mock_dispatcher)
    notifications.set_webpush_channel(mock_webpush)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, mock_dispatcher, mock_webpush, factory

    app.dependency_overrides.clear()
    notifications.set_dispatcher(None)
    notifications.set_webpush_channel(None)
    await engine.dispose()


@pytest.mark.asyncio
class TestNotificationRoutes:

    async def test_get_config(self, notif_client) -> None:
        client, _, _, _ = notif_client
        resp = await client.get("/notifications/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert data["channels"]["webpush"]["enabled"] is True

    async def test_put_config(self, notif_client) -> None:
        client, dispatcher, _, _ = notif_client
        resp = await client.put("/notifications/config", json={
            "channels": {"webpush": {"enabled": False, "min_severity": "error"}}
        })
        assert resp.status_code == 200
        dispatcher._config_service._save_toml.assert_called_once()
        dispatcher.reload.assert_called_once()

    async def test_get_channel_status(self, notif_client) -> None:
        client, _, _, _ = notif_client
        resp = await client.get("/notifications/channels/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"]["webpush"]["available"] is True
        assert data["channels"]["host_native"]["available"] is False

    async def test_get_vapid_public_key(self, notif_client) -> None:
        client, _, _, _ = notif_client
        resp = await client.get("/notifications/vapid-public-key")
        assert resp.status_code == 200
        assert resp.json()["public_key"] == "BLtest_vapid_public_key_data"

    async def test_subscribe_push(self, notif_client) -> None:
        client, _, webpush, _ = notif_client
        resp = await client.post("/notifications/push-subscription", json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/sub1",
            "keys": {"p256dh": "key1", "auth": "key2"},
        })
        assert resp.status_code == 201
        webpush.add_subscription.assert_called_once_with(
            "https://fcm.googleapis.com/fcm/send/sub1", "key1", "key2"
        )

    async def test_subscribe_push_duplicate_is_an_update(self, notif_client) -> None:
        """R3: re-sending a known subscription succeeds (re-enabling never gets a 409)."""
        client, _, webpush, _ = notif_client
        webpush.add_subscription.return_value = False
        resp = await client.post("/notifications/push-subscription", json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/dup",
            "keys": {"p256dh": "k", "auth": "a"},
        })
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Subscription updated"

    async def test_subscribe_push_keeps_endpoint_validation(self, notif_client) -> None:
        client, _, webpush, _ = notif_client
        resp = await client.post("/notifications/push-subscription", json={
            "endpoint": "https://192.168.1.10/steal",
            "keys": {"p256dh": "k", "auth": "a"},
        })
        assert resp.status_code == 422
        webpush.add_subscription.assert_not_called()

    async def test_subscribe_push_missing_keys(self, notif_client) -> None:
        client, _, _, _ = notif_client
        resp = await client.post("/notifications/push-subscription", json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/sub",
            "keys": {},
        })
        assert resp.status_code == 400

    async def test_unsubscribe_push(self, notif_client) -> None:
        client, _, webpush, _ = notif_client
        resp = await client.request("DELETE", "/notifications/push-subscription", json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/sub1",
            "keys": {"p256dh": "k", "auth": "a"},
        })
        assert resp.status_code == 200
        webpush.remove_subscription.assert_called_once()

    async def test_unsubscribe_push_not_found(self, notif_client) -> None:
        client, _, webpush, _ = notif_client
        webpush.remove_subscription.return_value = False
        resp = await client.request("DELETE", "/notifications/push-subscription", json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/nope",
            "keys": {"p256dh": "k", "auth": "a"},
        })
        assert resp.status_code == 404

    async def test_get_history(self, notif_client) -> None:
        client, _, _, factory = notif_client

        # Insert a log entry
        from datetime import datetime, timezone
        async with factory() as session:
            entry = NotificationLog(
                event_type="test",
                severity="info",
                title="Test",
                body="Test body",
                timestamp=datetime.now(timezone.utc),
                channels_delivered=json.dumps(["webpush"]),
            )
            session.add(entry)
            await session.commit()

        resp = await client.get("/notifications/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["event_type"] == "test"

    async def test_get_history_pagination(self, notif_client) -> None:
        client, _, _, factory = notif_client

        from datetime import datetime, timezone
        async with factory() as session:
            for i in range(5):
                entry = NotificationLog(
                    event_type="test",
                    severity="info",
                    title=f"Test {i}",
                    body="Body",
                    timestamp=datetime.now(timezone.utc),
                    channels_delivered=json.dumps([]),
                )
                session.add(entry)
            await session.commit()

        resp = await client.get("/notifications/history?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

    async def test_send_test_notification(self, notif_client) -> None:
        client, dispatcher, _, _ = notif_client
        resp = await client.post("/notifications/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "webpush" in data["channels_delivered"]
        dispatcher.dispatch.assert_called_once()
        assert dispatcher.dispatch.call_args.kwargs["only_channel"] is None

    async def test_send_test_to_one_channel(self, notif_client) -> None:
        """R7: the request's channel is honoured."""
        client, dispatcher, _, _ = notif_client
        dispatcher.dispatch.return_value = ([], {"host_native": "unavailable"})
        resp = await client.post("/notifications/test", json={"channel": "host_native"})
        assert resp.status_code == 200
        assert resp.json() == {"success": False, "channels_delivered": [], "errors": {"host_native": "unavailable"}}
        assert dispatcher.dispatch.call_args.kwargs["only_channel"] == "host_native"

    async def test_send_test_to_unknown_channel_404(self, notif_client) -> None:
        client, dispatcher, _, _ = notif_client
        resp = await client.post("/notifications/test", json={"channel": "email"})
        assert resp.status_code == 404
        dispatcher.dispatch.assert_not_called()

    async def test_not_initialized_503(self) -> None:
        """Routes return 503 when dispatcher/webpush not set."""
        notifications.set_dispatcher(None)
        notifications.set_webpush_channel(None)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
            resp = await client.get("/notifications/config")
            assert resp.status_code == 503


# --- With a real dispatcher and config file ---


class _Channel(NotificationChannelBase):
    def __init__(self, name: str, info: dict[str, object]) -> None:
        self._name, self._info = name, info
        self.sent: list[object] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send(self, event) -> None:
        self.sent.append(event)

    async def is_available(self) -> bool:
        return bool(self._info["available"])

    async def describe(self) -> dict[str, object]:
        return dict(self._info)


@pytest_asyncio.fixture
async def real_client(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config_path = tmp_path / "config.toml"
    dispatcher = NotificationDispatcher(ConfigService(config_path=config_path), factory)
    webpush = _Channel("webpush", {"available": False, "missing_dependencies": ["push_subscription"], "subscriptions": 0})
    host = _Channel("host_native", {
        "available": False, "host_os": "linux", "detection_method": "env_var",
        "missing_dependencies": ["dbus_socket"],
    })
    dispatcher.register_channel(webpush)
    dispatcher.register_channel(host)
    notifications.set_dispatcher(dispatcher)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=AUTH_HEADERS) as client:
        yield client, dispatcher, config_path, host
    notifications.set_dispatcher(None)
    await engine.dispose()


@pytest.mark.asyncio
class TestNotificationRoutesWithConfig:

    async def test_defaults(self, real_client) -> None:
        """R5: Web Push on at warning; Host Native off (it needs a desktop session),
        and so are webhook, ntfy and email (they need an address first)."""
        client, _, _, _ = real_client
        data = (await client.get("/notifications/config")).json()
        assert data["channels"]["webpush"] == {"enabled": True, "min_severity": "warning"}
        assert data["channels"]["host_native"] == {"enabled": False, "min_severity": "warning"}
        for name in ("webhook", "ntfy", "email"):
            assert data["channels"][name]["enabled"] is False
            assert data["channels"][name]["min_severity"] == "warning"

    async def test_partial_update_changes_only_the_given_field(self, real_client) -> None:
        """R5 AC2: {min_severity} alone must not enable a disabled channel."""
        client, _, config_path, _ = real_client
        resp = await client.put("/notifications/config", json={"channels": {"host_native": {"min_severity": "error"}}})
        assert resp.status_code == 200
        assert resp.json()["channels"]["host_native"] == {"enabled": False, "min_severity": "error"}
        assert toml.loads(config_path.read_text())["notifications"]["channels"]["host_native"] == {
            "enabled": False, "min_severity": "error",
        }

        resp = await client.put("/notifications/config", json={"channels": {"host_native": {"enabled": True}}})
        assert resp.json()["channels"]["host_native"] == {"enabled": True, "min_severity": "error"}

    async def test_update_of_unknown_channel_400(self, real_client) -> None:
        client, _, config_path, _ = real_client
        resp = await client.put("/notifications/config", json={"channels": {"hostnative": {"enabled": True}}})
        assert resp.status_code == 400
        assert not config_path.exists()

    async def test_update_with_unreadable_config_saves_nothing(self, real_client) -> None:
        client, _, config_path, _ = real_client
        config_path.write_text("[broken")
        resp = await client.put("/notifications/config", json={"channels": {"webpush": {"enabled": False}}})
        assert resp.status_code == 500
        assert "broken" not in resp.text and "Invalid TOML" not in resp.text
        assert config_path.read_text() == "[broken"

    async def test_status_reports_platform_checks(self, real_client) -> None:
        """R4/R6: what is missing, the host OS and how it was detected reach the API."""
        client, _, _, _ = real_client
        data = (await client.get("/notifications/channels/status")).json()
        assert data["channels"]["host_native"] == {
            "available": False, "detection_method": "env_var", "host_os": "linux",
            "missing_dependencies": ["dbus_socket"], "permission_status": None, "subscriptions": None,
        }
        assert data["channels"]["webpush"]["missing_dependencies"] == ["push_subscription"]
        assert data["channels"]["webpush"]["subscriptions"] == 0
        assert data["config_error"] is False
        assert data["unknown_channels"] == []

    async def test_status_reports_config_errors(self, real_client) -> None:
        """R8: a broken settings file and unknown channel names reach the settings page."""
        client, dispatcher, config_path, _ = real_client
        config_path.write_text(toml.dumps({"notifications": {"channels": {
            "webpush": {"min_severity": "loud"}, "e-mail": {"enabled": True},
        }}}))
        dispatcher.reload()
        data = (await client.get("/notifications/channels/status")).json()
        assert data["config_error"] is True
        assert data["unknown_channels"] == ["e-mail"]

    async def test_test_of_one_unavailable_channel(self, real_client) -> None:
        client, _, _, host = real_client
        data = (await client.post("/notifications/test", json={"channel": "host_native"})).json()
        assert data == {"success": False, "channels_delivered": [], "errors": {"host_native": "unavailable"}}
        assert host.sent == []

    async def test_test_of_one_disabled_available_channel(self, real_client) -> None:
        client, _, _, host = real_client
        host._info = {"available": True}
        data = (await client.post("/notifications/test", json={"channel": "host_native"})).json()
        assert data == {"success": True, "channels_delivered": ["host_native"], "errors": {}}
        assert len(host.sent) == 1

    @pytest.mark.parametrize("severity", ["critical", "WARNING", "", "warn"])
    async def test_update_with_invalid_min_severity_422(self, real_client, severity) -> None:
        """An unknown min_severity is refused before anything is written."""
        client, _, config_path, _ = real_client
        resp = await client.put("/notifications/config", json={"channels": {"webpush": {"min_severity": "error"}}})
        assert resp.status_code == 200
        saved = config_path.read_text()

        resp = await client.put("/notifications/config", json={
            "channels": {"webpush": {"enabled": False, "min_severity": severity}},
        })
        assert resp.status_code == 422
        assert config_path.read_text() == saved
        data = (await client.get("/notifications/config")).json()
        assert data["channels"]["webpush"] == {"enabled": True, "min_severity": "error"}


async def _add_log_rows(factory, slugs: list[str | None]) -> None:
    from datetime import datetime, timezone
    async with factory() as session:
        for i, slug in enumerate(slugs):
            session.add(NotificationLog(
                event_type="sync_completed", severity="info", title=f"Entry {i}", body="",
                timestamp=datetime.now(timezone.utc), channels_delivered=json.dumps([]),
                profile_slug=slug,
            ))
        await session.commit()


@pytest.mark.asyncio
class TestNotificationHistoryProfileFilter:

    async def test_history_filters_by_profile(self, notif_client) -> None:
        client, _, _, factory = notif_client
        await _add_log_rows(factory, ["docs", "photos", None, "docs", "docs-2"])

        data = (await client.get("/notifications/history", params={"profile": "docs"})).json()
        assert data["total"] == 2
        # Newest first; only exact slug matches ("docs-2" is another profile).
        assert [item["title"] for item in data["items"]] == ["Entry 3", "Entry 0"]

        data = (await client.get("/notifications/history", params={"profile": "photos"})).json()
        assert (data["total"], [i["title"] for i in data["items"]]) == (1, ["Entry 1"])

    async def test_history_filter_pages_within_the_profile(self, notif_client) -> None:
        client, _, _, factory = notif_client
        await _add_log_rows(factory, ["docs", "other", "docs", "other", "docs"])

        data = (await client.get("/notifications/history", params={"profile": "docs", "limit": 1, "offset": 1})).json()
        assert data["total"] == 3
        assert [i["title"] for i in data["items"]] == ["Entry 2"]

    async def test_history_of_unknown_profile_is_empty(self, notif_client) -> None:
        client, _, _, factory = notif_client
        await _add_log_rows(factory, ["docs"])
        resp = await client.get("/notifications/history", params={"profile": "nope"})
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    async def test_history_without_profile_returns_all(self, notif_client) -> None:
        client, _, _, factory = notif_client
        await _add_log_rows(factory, ["docs", None, "photos"])
        data = (await client.get("/notifications/history")).json()
        assert data["total"] == 3
        assert [i["title"] for i in data["items"]] == ["Entry 2", "Entry 1", "Entry 0"]
