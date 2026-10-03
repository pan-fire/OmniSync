"""Tests for WebPushChannel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, PushSubscription
from backend.services.notification_channels import webpush as webpush_module
from backend.services.notification_channels.webpush import WebPushChannel
from backend.services.notification_events import test_event as make_test_event  # aliased: pytest would collect "test_event"


@pytest_asyncio.fixture
async def webpush_deps(tmp_path):
    """Provide a WebPushChannel with in-memory DB and temp VAPID dir."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vapid_dir = tmp_path / "vapid"
    channel = WebPushChannel(factory, vapid_dir=vapid_dir)
    yield channel, factory
    await engine.dispose()


@pytest.mark.asyncio
class TestWebPushChannel:

    async def test_channel_name(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        assert channel.channel_name == "webpush"

    async def test_ensure_vapid_keys_generates(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        assert channel._public_key is not None
        assert len(channel._public_key) > 0
        assert channel._private_key_path.exists()
        assert channel._public_key_path.exists()

    async def test_ensure_vapid_keys_idempotent(self, webpush_deps) -> None:
        """Property P6: Calling ensure_vapid_keys() N times reads same keys."""
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        key1 = channel._public_key
        channel.ensure_vapid_keys()
        key2 = channel._public_key
        channel.ensure_vapid_keys()
        key3 = channel._public_key
        assert key1 == key2 == key3

    async def test_keys_are_written_atomically(self, webpush_deps, monkeypatch) -> None:
        """Both key files go through the atomic writer; the private key owner-only."""
        channel, _ = webpush_deps
        writes: list[tuple[str, int]] = []
        real = webpush_module.atomic_write_file

        def spy(path, data, mode=0o600):
            writes.append((path.name, mode))
            real(path, data, mode)

        monkeypatch.setattr(webpush_module, "atomic_write_file", spy)
        channel.ensure_vapid_keys()
        assert writes == [("private_key.pem", 0o600), ("public_key.txt", 0o644)]
        assert channel._private_key_path.stat().st_mode & 0o777 == 0o600
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        assert load_pem_private_key(channel._private_key_path.read_bytes(), password=None)

    async def test_failed_key_conversion_keeps_the_old_file(self, webpush_deps, monkeypatch) -> None:
        """A crash while rewriting a PEM public key leaves the old file whole."""
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key
        private = load_pem_private_key(channel._private_key_path.read_bytes(), password=None)
        pem = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        channel._public_key_path.write_text(pem)
        channel._public_key = pem

        def disk_full(fd: int) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("backend.security.os.fsync", disk_full)
        with pytest.raises(OSError, match="No space left"):
            channel.get_public_key()
        assert channel._public_key_path.read_text() == pem
        assert sorted(p.name for p in channel._vapid_dir.iterdir()) == ["private_key.pem", "public_key.txt"]

    async def test_get_public_key(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        key = channel.get_public_key()
        assert isinstance(key, str)
        assert len(key) > 10

    async def test_is_available_with_keys_and_a_subscription(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/sub", "k", "a")
        assert await channel.is_available() is True

    async def test_not_available_without_a_subscription(self, webpush_deps) -> None:
        """No browser subscribed: nothing could receive a push (reported honestly)."""
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        assert await channel.is_available() is False
        assert await channel.describe() == {
            "available": False, "missing_dependencies": ["push_subscription"], "subscriptions": 0,
        }

    async def test_describe_without_keys(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        info = await channel.describe()
        assert info["available"] is False
        assert info["missing_dependencies"] == ["vapid_keys"]

    async def test_is_available_without_keys(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        assert await channel.is_available() is False

    async def test_add_subscription(self, webpush_deps) -> None:
        channel, factory = webpush_deps
        await channel.add_subscription(
            "https://push.example.com/sub1", "p256dh_key_data", "auth_key_data"
        )
        async with factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(PushSubscription))
            subs = result.scalars().all()
            assert len(subs) == 1
            assert subs[0].endpoint == "https://push.example.com/sub1"

    async def test_add_duplicate_updates_the_keys(self, webpush_deps) -> None:
        """R3: re-subscribing the same browser is idempotent (no 409 on re-enable)."""
        channel, factory = webpush_deps
        assert await channel.add_subscription("https://push.example.com/dup", "k", "a") is True
        assert await channel.add_subscription("https://push.example.com/dup", "k2", "a2") is False
        async with factory() as session:
            from sqlalchemy import select
            subs = (await session.execute(select(PushSubscription))).scalars().all()
        assert [(s.p256dh_key, s.auth_key) for s in subs] == [("k2", "a2")]

    async def test_remove_subscription(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        await channel.add_subscription("https://push.example.com/rm", "k", "a")
        assert await channel.remove_subscription("https://push.example.com/rm") is True

    async def test_remove_nonexistent(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        assert await channel.remove_subscription("https://push.example.com/nope") is False

    async def test_send_no_subscriptions(self, webpush_deps) -> None:
        """send() with no subscriptions raises RuntimeError."""
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        with pytest.raises(RuntimeError, match="No push subscriptions"):
            await channel.send(make_test_event())

    async def test_send_calls_webpush(self, webpush_deps) -> None:
        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/send", "pk", "ak")

        with patch("pywebpush.webpush") as mock_wp:
            await channel.send(make_test_event())
            mock_wp.assert_called_once()
            call_kwargs = mock_wp.call_args
            assert call_kwargs[1]["subscription_info"]["endpoint"] == "https://push.example.com/send"

    async def test_send_runs_off_the_event_loop_with_a_timeout(self, webpush_deps) -> None:
        """ARC-2: the synchronous pywebpush call never blocks the loop."""
        import threading

        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/send", "pk", "ak")
        seen: dict[str, object] = {}

        def fake_webpush(**kwargs):
            seen["thread"] = threading.current_thread()
            seen["timeout"] = kwargs.get("timeout")

        with patch("pywebpush.webpush", side_effect=fake_webpush):
            await channel.send(make_test_event())

        assert seen["thread"] is not threading.main_thread()
        assert seen["timeout"] == webpush_module.PUSH_TIMEOUT

    async def test_hung_endpoint_is_abandoned(self, webpush_deps, monkeypatch) -> None:
        import asyncio
        import time

        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/hung", "pk", "ak")
        monkeypatch.setattr(webpush_module, "PUSH_TIMEOUT", -4.8)  # outer bound 0.2 s

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        with patch("pywebpush.webpush", side_effect=lambda **_: time.sleep(1)):
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="failed for all 1"):
                await channel.send(make_test_event())  # raises after the bound
            elapsed = time.monotonic() - started
        tick_task.cancel()

        assert elapsed < 0.9
        assert ticks > 5  # the loop kept running meanwhile

    async def test_send_removes_expired_410(self, webpush_deps) -> None:
        """Property P5: 410 response deletes subscription from DB."""
        channel, factory = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/expired", "pk", "ak")

        # Mock a 410 response
        from pywebpush import WebPushException
        mock_response = MagicMock()
        mock_response.status_code = 410

        exc = WebPushException("Gone")
        exc.response = mock_response

        with patch("pywebpush.webpush", side_effect=exc):
            with pytest.raises(RuntimeError, match="expired"):
                await channel.send(make_test_event())

        # Verify subscription was removed
        async with factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(PushSubscription))
            assert len(result.scalars().all()) == 0

    async def test_all_subscriptions_failing_raises(self, webpush_deps) -> None:
        """R7: a push that reached no browser is not reported as delivered."""
        from pywebpush import WebPushException

        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/a", "pk", "ak")
        await channel.add_subscription("https://push.example.com/b", "pk", "ak")
        with patch("pywebpush.webpush", side_effect=WebPushException("500 Server Error")):
            with pytest.raises(RuntimeError, match="failed for all 2"):
                await channel.send(make_test_event())

    async def test_one_subscription_delivering_is_enough(self, webpush_deps) -> None:
        from pywebpush import WebPushException

        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        await channel.add_subscription("https://push.example.com/bad", "pk", "ak")
        await channel.add_subscription("https://push.example.com/good", "pk", "ak")

        def fake(**kwargs):
            if kwargs["subscription_info"]["endpoint"].endswith("/bad"):
                raise WebPushException("500 Server Error")

        with patch("pywebpush.webpush", side_effect=fake):
            await channel.send(make_test_event())  # no exception

    async def test_subscriptions_are_pushed_concurrently(self, webpush_deps) -> None:
        import time

        channel, _ = webpush_deps
        channel.ensure_vapid_keys()
        for i in range(3):
            await channel.add_subscription(f"https://push.example.com/{i}", "pk", "ak")
        with patch("pywebpush.webpush", side_effect=lambda **_: time.sleep(0.3)):
            started = time.monotonic()
            await channel.send(make_test_event())
            assert time.monotonic() - started < 0.8  # not 0.9 s one after the other
