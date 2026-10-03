"""Tests for NotificationDispatcher."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
import pytest_asyncio
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, NotificationLog
from backend.services.config import ConfigService
from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_dispatcher import (
    ERROR_FAILED,
    ERROR_TIMEOUT,
    ERROR_UNAVAILABLE,
    MAX_LOG_ROWS,
    NotificationDispatcher,
)
from backend.services.notification_events import (
    SEVERITY_ORDER,
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
    test_event as make_test_event,  # aliased: pytest would collect "test_event"
)


class FakeChannel(NotificationChannelBase):
    """Test channel that tracks calls."""

    def __init__(self, name: str = "fake", available: bool = True) -> None:
        self._name = name
        self._available = available
        self.sent: list[NotificationEvent] = []
        self._send_error: Exception | None = None

    @property
    def channel_name(self) -> str:
        return self._name

    async def send(self, event: NotificationEvent) -> None:
        if self._send_error:
            raise self._send_error
        self.sent.append(event)

    async def is_available(self) -> bool:
        return self._available


@pytest_asyncio.fixture
async def dispatcher_deps(tmp_path):
    """Provide a NotificationDispatcher with in-memory DB and temp config."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    config_path = tmp_path / "config.toml"
    config_service = ConfigService(config_path=config_path)

    dispatcher = NotificationDispatcher(config_service, factory)
    yield dispatcher, factory, config_service, config_path
    await engine.dispose()


@pytest.mark.asyncio
class TestNotificationDispatcher:

    async def test_register_channel(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        ch = FakeChannel("test_ch")
        dispatcher.register_channel(ch)
        assert len(dispatcher._channels) == 1

    async def test_dispatch_sends_to_enabled_channel(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)
        # Default config enables webpush at warning severity
        # TEST events bypass severity filtering, so should be delivered
        event = make_test_event()
        delivered, _ = await dispatcher.dispatch(event)
        assert "webpush" in delivered

    async def test_dispatch_filters_by_severity(self, dispatcher_deps) -> None:
        """Property P1: send() called iff severity >= min_severity and enabled."""
        dispatcher, _, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)

        # Set min_severity to debug so INFO test event passes
        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": True, "min_severity": "debug"}}}
        }))
        dispatcher.reload()

        event = make_test_event()  # severity = INFO
        delivered, _ = await dispatcher.dispatch(event)
        assert "webpush" in delivered
        assert len(ch.sent) == 1

    async def test_dispatch_skips_disabled_channel(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)

        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": False, "min_severity": "debug"}}}
        }))
        dispatcher.reload()

        delivered, _ = await dispatcher.dispatch(make_test_event())
        assert len(delivered) == 0
        assert len(ch.sent) == 0

    async def test_channel_failure_doesnt_block_others(self, dispatcher_deps) -> None:
        """Property P2: If channel A raises, channel B still called."""
        dispatcher, _, _, config_path = dispatcher_deps

        ch_a = FakeChannel("webpush")
        ch_a._send_error = RuntimeError("boom")
        ch_b = FakeChannel("host_native")

        dispatcher.register_channel(ch_a)
        dispatcher.register_channel(ch_b)

        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {
                "webpush": {"enabled": True, "min_severity": "debug"},
                "host_native": {"enabled": True, "min_severity": "debug"},
            }}
        }))
        dispatcher.reload()

        delivered, errors = await dispatcher.dispatch(make_test_event())
        assert "host_native" in delivered
        assert "webpush" not in delivered
        assert "webpush" in errors
        assert len(ch_b.sent) == 1

    async def test_dispatch_writes_log_entry(self, dispatcher_deps) -> None:
        """Property P3: Every dispatched event produces exactly one NotificationLog row."""
        dispatcher, factory, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)

        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": True, "min_severity": "debug"}}}
        }))
        dispatcher.reload()

        await dispatcher.dispatch(make_test_event())

        async with factory() as session:
            result = await session.execute(select(NotificationLog))
            logs = result.scalars().all()
            assert len(logs) == 1
            assert logs[0].event_type == "test"

    async def test_log_eviction(self, dispatcher_deps) -> None:
        """Property P4: Insert > MAX_LOG_ROWS events → table capped at MAX_LOG_ROWS."""
        dispatcher, factory, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)

        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": True, "min_severity": "debug"}}}
        }))
        dispatcher.reload()

        for _ in range(MAX_LOG_ROWS + 5):
            await dispatcher.dispatch(make_test_event())

        async with factory() as session:
            from sqlalchemy import func
            count = await session.execute(select(func.count(NotificationLog.id)))
            total = count.scalar()
            assert total <= MAX_LOG_ROWS

    async def test_unavailable_channel_not_sent(self, dispatcher_deps) -> None:
        """Property P10: Unavailable channel is never sent to."""
        dispatcher, _, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush", available=False)
        dispatcher.register_channel(ch)

        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": True, "min_severity": "debug"}}}
        }))
        dispatcher.reload()

        delivered, _ = await dispatcher.dispatch(make_test_event())
        assert len(delivered) == 0
        assert len(ch.sent) == 0

    async def test_default_config_when_missing(self, dispatcher_deps) -> None:
        """Property P8: Missing [notifications] section uses defaults."""
        dispatcher, _, _, _ = dispatcher_deps
        cfg = dispatcher.get_config()
        assert "webpush" in cfg
        assert cfg["webpush"]["enabled"] is True
        assert cfg["webpush"]["min_severity"] == "warning"
        assert "host_native" in cfg
        # Off by default (spec R5 AC5): it needs a desktop session the
        # default Docker deployment does not have.
        assert cfg["host_native"]["enabled"] is False

    async def test_reload_updates_config(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        import toml
        config_path.write_text(toml.dumps({
            "notifications": {"channels": {"webpush": {"enabled": False, "min_severity": "error"}}}
        }))
        dispatcher.reload()

        cfg = dispatcher.get_config()
        assert cfg["webpush"]["enabled"] is False
        assert cfg["webpush"]["min_severity"] == "error"

    async def test_get_channel_status(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        ch = FakeChannel("webpush", available=True)
        dispatcher.register_channel(ch)

        status = await dispatcher.get_channel_status()
        assert status["webpush"]["available"] is True


class SlowChannel(FakeChannel):
    """A channel whose send takes ``delay`` seconds."""

    def __init__(self, name: str, delay: float) -> None:
        super().__init__(name)
        self._delay = delay

    async def send(self, event: NotificationEvent) -> None:
        await asyncio.sleep(self._delay)
        self.sent.append(event)


class DescribedChannel(FakeChannel):
    def __init__(self, name: str, missing: list[str]) -> None:
        super().__init__(name, available=not missing)
        self._missing = missing

    async def describe(self) -> dict[str, object]:
        return {"available": not self._missing, "missing_dependencies": self._missing}


def _enable(config_path, **channels: bool) -> None:
    import toml
    config_path.write_text(toml.dumps({"notifications": {"channels": {
        name: {"enabled": on, "min_severity": "debug"} for name, on in channels.items()
    }}}))


@pytest.mark.asyncio
class TestConcurrentDispatch:
    """R2: channels are sent to concurrently, each with a timeout."""

    async def test_channels_are_sent_to_concurrently(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        a, b = SlowChannel("a", 0.3), SlowChannel("b", 0.3)
        dispatcher.register_channel(a)
        dispatcher.register_channel(b)
        _enable(config_path, a=True, b=True)
        dispatcher.reload()

        started = time.monotonic()
        delivered, errors = await dispatcher.dispatch(make_test_event())
        assert time.monotonic() - started < 0.55  # not 0.6 s one after the other
        assert sorted(delivered) == ["a", "b"] and errors == {}

    async def test_hung_channel_times_out_and_does_not_hold_up_others(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        dispatcher._channel_timeout = 0.2
        hung, fast = SlowChannel("webpush", 30), FakeChannel("host_native")
        dispatcher.register_channel(hung)  # registered first, as in main.py
        dispatcher.register_channel(fast)
        _enable(config_path, webpush=True, host_native=True)
        dispatcher.reload()

        started = time.monotonic()
        delivered, errors = await dispatcher.dispatch(make_test_event())
        assert time.monotonic() - started < 1.0
        assert delivered == ["host_native"]
        assert errors == {"webpush": ERROR_TIMEOUT}
        assert len(fast.sent) == 1

    async def test_errors_are_codes_without_exception_text(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        ch = FakeChannel("webpush")
        ch._send_error = RuntimeError("notify-send failed: /secret/path")
        dispatcher.register_channel(ch)
        _enable(config_path, webpush=True)
        dispatcher.reload()

        _, errors = await dispatcher.dispatch(make_test_event())
        assert errors == {"webpush": ERROR_FAILED}

    async def test_unavailable_channel_is_reported_and_logged_once(self, dispatcher_deps, caplog) -> None:
        """R4 AC4: an unavailable channel is skipped with a logged warning."""
        dispatcher, _, _, config_path = dispatcher_deps
        dispatcher.register_channel(FakeChannel("host_native", available=False))
        _enable(config_path, host_native=True)
        dispatcher.reload()

        with caplog.at_level(logging.WARNING, logger="backend.services.notification_dispatcher"):
            for _ in range(3):
                _, errors = await dispatcher.dispatch(make_test_event())
                assert errors == {"host_native": ERROR_UNAVAILABLE}
        warnings = [r for r in caplog.records if "unavailable" in r.getMessage()]
        assert len(warnings) == 1

    async def test_only_channel_tests_one_channel_even_if_disabled(self, dispatcher_deps) -> None:
        """R7: a per-channel test goes to that channel only."""
        dispatcher, _, _, config_path = dispatcher_deps
        webpush, host = FakeChannel("webpush"), FakeChannel("host_native")
        dispatcher.register_channel(webpush)
        dispatcher.register_channel(host)
        _enable(config_path, webpush=True, host_native=False)
        dispatcher.reload()

        delivered, errors = await dispatcher.dispatch(make_test_event(), only_channel="host_native")
        assert delivered == ["host_native"] and errors == {}
        assert len(host.sent) == 1 and webpush.sent == []

    async def test_emit_dispatches_in_the_background(self, dispatcher_deps) -> None:
        dispatcher, factory, _, config_path = dispatcher_deps
        slow = SlowChannel("webpush", 0.2)
        dispatcher.register_channel(slow)
        _enable(config_path, webpush=True)
        dispatcher.reload()

        started = time.monotonic()
        dispatcher.emit(make_test_event())
        assert time.monotonic() - started < 0.1  # the caller does not wait
        assert slow.sent == []
        await dispatcher.drain(timeout=5)
        assert len(slow.sent) == 1
        async with factory() as session:
            assert len((await session.execute(select(NotificationLog))).scalars().all()) == 1


@pytest.mark.asyncio
class TestConfigValidation:
    """R5 defaults and R8 startup validation."""

    async def test_malformed_config_is_logged_and_flagged(self, dispatcher_deps, caplog) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        config_path.write_text("[notifications\nbroken = ")
        with caplog.at_level(logging.ERROR, logger="backend.services.notification_dispatcher"):
            dispatcher.reload()
        assert dispatcher.config_error is True
        assert dispatcher.get_config()["webpush"]["enabled"] is True  # defaults
        assert any("could not be read" in r.getMessage() for r in caplog.records)

    async def test_invalid_values_fall_back_to_defaults(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        import toml
        config_path.write_text(toml.dumps({"notifications": {"channels": {
            "webpush": {"enabled": "yes", "min_severity": "loud"},
            "host_native": {"enabled": True, "min_severity": "error"},
        }}}))
        dispatcher.reload()
        cfg = dispatcher.get_config()
        assert cfg["webpush"] == {"enabled": True, "min_severity": "warning"}
        assert cfg["host_native"] == {"enabled": True, "min_severity": "error"}
        assert dispatcher.config_error is True

    async def test_valid_config_has_no_error(self, dispatcher_deps) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        _enable(config_path, webpush=False)
        dispatcher.reload()
        assert dispatcher.config_error is False

    async def test_validate_channels_warns_about_enabled_unavailable_channels(
        self, dispatcher_deps, caplog,
    ) -> None:
        dispatcher, _, _, config_path = dispatcher_deps
        dispatcher.register_channel(DescribedChannel("webpush", ["push_subscription"]))
        dispatcher.register_channel(DescribedChannel("host_native", ["dbus_socket", "notify-send"]))
        _enable(config_path, webpush=False, host_native=True, hostnative=True)
        dispatcher.reload()

        with caplog.at_level(logging.WARNING, logger="backend.services.notification_dispatcher"):
            problems = await dispatcher.validate_channels()
        # Only the enabled channel counts; the disabled one is not a problem.
        assert problems == {"host_native": ["dbus_socket", "notify-send"]}
        text = caplog.text
        assert "host_native" in text and "dbus_socket, notify-send" in text
        assert "unknown channel 'hostnative'" in text
        assert dispatcher.unknown_channels() == ["hostnative"]

    async def test_channel_status_carries_what_is_missing(self, dispatcher_deps) -> None:
        dispatcher, _, _, _ = dispatcher_deps
        dispatcher.register_channel(DescribedChannel("host_native", ["notify-send"]))
        status = await dispatcher.get_channel_status()
        assert status["host_native"] == {"available": False, "missing_dependencies": ["notify-send"]}


@pytest.mark.asyncio
class TestDispatcherSeverityProperty:
    """Property-based tests for severity threshold filtering."""

    @given(
        event_sev=st.sampled_from(list(NotificationSeverity)),
        min_sev=st.sampled_from(list(NotificationSeverity)),
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    async def test_severity_threshold_property(self, event_sev, min_sev) -> None:
        """Property P1: send() called iff event severity >= channel min_severity."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        import tempfile
        from pathlib import Path
        import toml

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.toml"
            config_path.write_text(toml.dumps({
                "notifications": {"channels": {"test_ch": {"enabled": True, "min_severity": min_sev.value}}}
            }))
            config_service = ConfigService(config_path=config_path)
            dispatcher = NotificationDispatcher(config_service, factory)

            ch = FakeChannel("test_ch")
            dispatcher.register_channel(ch)

            from backend.services.notification_events import NotificationEvent
            from datetime import datetime, timezone
            # Use a non-TEST event type since TEST bypasses severity
            event = NotificationEvent(
                severity=event_sev,
                event_type=NotificationEventType.SYNC_COMPLETED,
                title="T",
                body="B",
                timestamp=datetime.now(timezone.utc),
            )

            await dispatcher.dispatch(event)

            should_deliver = SEVERITY_ORDER[event_sev] >= SEVERITY_ORDER[min_sev]
            was_delivered = len(ch.sent) > 0
            assert should_deliver == was_delivered

        await engine.dispose()


@pytest.mark.asyncio
class TestDispatcherProfileContext:
    """multi-sync-profiles 13.4: the log row carries the event's profile slug."""

    async def test_dispatch_writes_profile_slug_to_log(self, dispatcher_deps) -> None:
        from backend.services.notification_events import sync_completed_event, sync_failed_event

        dispatcher, factory, _, _ = dispatcher_deps
        ch = FakeChannel("webpush")
        dispatcher.register_channel(ch)

        # Delivered (error >= warning) and not delivered (info < warning):
        # both are logged with their profile.
        await dispatcher.dispatch(sync_failed_event("push", "boom", profile_name="Work Docs", profile_slug="work-docs"))
        await dispatcher.dispatch(sync_completed_event("pull", 3, profile_name="Photos", profile_slug="photos"))
        await dispatcher.dispatch(make_test_event())  # not tied to a profile

        async with factory() as session:
            logs = (await session.execute(select(NotificationLog).order_by(NotificationLog.id))).scalars().all()
        assert [(log.event_type, log.profile_slug) for log in logs] == [
            ("sync_failed", "work-docs"), ("sync_completed", "photos"), ("test", None),
        ]
        assert logs[0].title.endswith("— Work Docs")
        assert [len(ch.sent), logs[0].channels_delivered, logs[1].channels_delivered] == [2, '["webpush"]', "[]"]
