"""NotificationDispatcher on bad input: broken config, flaky channels, a failing log.

A notification problem must never fail the sync that raised the event:
each case below is logged (and flagged in the settings), and the rest of
the dispatch carries on.
"""

from __future__ import annotations

import logging
import os
import stat
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from backend.db.models import NotificationLog
from backend.services.notification_dispatcher import ERROR_UNAVAILABLE, NotificationDispatcher
from backend.services.notification_events import NotificationEventType, NotificationSeverity
from backend.services.notification_events import test_event as make_test_event
from backend.tests import test_notification_dispatcher as base
from backend.tests.test_notification_dispatcher import FakeChannel

dispatcher_deps = base.dispatcher_deps


def warning_event():
    event = make_test_event()
    event.event_type = NotificationEventType.SYNC_FAILED
    event.severity = NotificationSeverity.WARNING
    return event


class TestBrokenConfig:
    async def test_an_unreadable_config_falls_back_to_the_defaults(self, dispatcher_deps, caplog) -> None:
        _, factory, config_service, _ = dispatcher_deps
        config_service._load_toml = MagicMock(side_effect=OSError("permission denied"))

        dispatcher = NotificationDispatcher(config_service, factory)

        assert dispatcher.config_error
        assert dispatcher.get_config()["webpush"]["enabled"] is True
        assert "could not be read" in caplog.text

    @pytest.mark.parametrize("toml", [
        'notifications = { channels = "webpush" }\n',
        "[notifications]\nchannels = 3\n",
    ])
    async def test_channels_that_are_not_a_table(self, dispatcher_deps, toml) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text(toml)

        dispatcher.reload()

        assert dispatcher.config_error
        assert dispatcher.get_config()["host_native"]["enabled"] is False

    async def test_a_channel_section_that_is_not_a_table(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text('[notifications.channels]\nwebpush = "off"\n')

        dispatcher.reload()

        assert dispatcher.config_error
        assert dispatcher.get_config()["webpush"] == {"enabled": True, "min_severity": "warning"}

    async def test_a_non_boolean_enabled_is_ignored(self, dispatcher_deps) -> None:
        """A string such as "yes" must not be read as truthy and turn a channel on."""
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text('[notifications.channels.host_native]\nenabled = "yes"\n')

        dispatcher.reload()

        assert dispatcher.config_error
        assert dispatcher.get_config()["host_native"]["enabled"] is False

    async def test_a_hand_edited_config_with_credentials_is_made_private(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text('[notifications.channels.ntfy]\nenabled = true\ntoken = "tk_fake_value_123"\n')
        os.chmod(config_path, 0o644)

        dispatcher.reload()

        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    async def test_a_chmod_failure_is_logged_not_raised(self, dispatcher_deps, caplog, monkeypatch) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text('[notifications.channels.ntfy]\ntoken = "tk_fake_value_123"\n')
        os.chmod(config_path, 0o644)

        def refuse(*args, **kwargs):
            raise PermissionError("read-only file system")

        monkeypatch.setattr("backend.services.notification_dispatcher.os.chmod", refuse)
        dispatcher.reload()

        assert "Could not restrict the permissions" in caplog.text

    async def test_unknown_channels_and_errors_are_reported_at_startup(self, dispatcher_deps, caplog) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text('[notifications.channels.pager]\nenabled = true\nmin_severity = "loud"\n')
        dispatcher.reload()

        with caplog.at_level(logging.WARNING):
            assert await dispatcher.validate_channels() == {}

        assert dispatcher.unknown_channels() == ["pager"]
        assert "unknown channel 'pager'" in caplog.text
        assert "settings have errors" in caplog.text


class TestFlakyChannels:
    async def test_a_raising_availability_check_counts_as_unavailable(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps

        class Broken(FakeChannel):
            async def is_available(self) -> bool:
                raise RuntimeError("dbus gone")

        ch = Broken("webpush")
        dispatcher.register_channel(ch)

        delivered, errors = await dispatcher.dispatch(warning_event())

        assert delivered == [] and errors == {"webpush": ERROR_UNAVAILABLE}
        assert ch.sent == []

    async def test_a_channel_that_comes_back_delivers_again(self, dispatcher_deps, caplog) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        ch = FakeChannel("webpush", available=False)
        dispatcher.register_channel(ch)
        await dispatcher.dispatch(warning_event())

        ch._available = True
        with caplog.at_level(logging.INFO):
            delivered, _ = await dispatcher.dispatch(warning_event())

        assert delivered == ["webpush"] and len(ch.sent) == 1
        assert "available again" in caplog.text

    async def test_a_describe_that_raises_reads_as_unavailable(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps

        class Opaque(FakeChannel):
            async def describe(self):
                raise RuntimeError("no status")

        dispatcher.register_channel(Opaque("webpush"))

        assert await dispatcher.get_channel_status() == {"webpush": {"available": False}}

    async def test_an_unknown_severity_in_the_config_means_warning(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)
        dispatcher._channel_config["webpush"] = {"enabled": True, "min_severity": "loud"}
        info = warning_event()
        info.severity = NotificationSeverity.INFO

        await dispatcher.dispatch(info)
        await dispatcher.dispatch(warning_event())

        assert [e.severity for e in ch.sent] == [NotificationSeverity.WARNING]

    async def test_a_failing_background_dispatch_is_logged(self, dispatcher_deps, caplog, monkeypatch) -> None:
        dispatcher, _, _, _ = dispatcher_deps

        async def boom(event, only_channel=None):
            raise RuntimeError("dispatch broke")

        monkeypatch.setattr(dispatcher, "dispatch", boom)
        dispatcher.emit(warning_event())
        await dispatcher.drain(timeout=5)

        assert "Could not dispatch notification" in caplog.text


async def test_a_log_write_failure_does_not_fail_the_dispatch(dispatcher_deps, caplog) -> None:
    """The notification was delivered; losing its log row must not turn that into an error."""
    dispatcher, factory, _, _ = dispatcher_deps
    ch = FakeChannel("webpush")
    dispatcher.register_channel(ch)

    def broken_factory():
        raise RuntimeError("database is locked")

    dispatcher._db_session_factory = broken_factory  # type: ignore[assignment]
    delivered, errors = await dispatcher.dispatch(warning_event())

    assert delivered == ["webpush"] and errors == {}
    assert "Failed to write notification log" in caplog.text
    async with factory() as session:
        assert (await session.execute(select(NotificationLog))).scalars().all() == []
