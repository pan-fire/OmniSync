"""Web Push notification channel using VAPID keys and pywebpush."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import PushSubscription
from backend.security import atomic_write_file
from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_events import NotificationEvent

logger = logging.getLogger(__name__)

DEFAULT_VAPID_DIR = Path(os.environ.get("OMNISYNC_VAPID_DIR", "/data/omnisync/vapid"))

# Seconds one push request may take (requests timeout; the call is also bounded).
PUSH_TIMEOUT = 10


class WebPushChannel(NotificationChannelBase):
    """Delivers notifications via the Web Push protocol using VAPID authentication."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        vapid_dir: Path = DEFAULT_VAPID_DIR,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._vapid_dir = vapid_dir
        self._private_key_path = vapid_dir / "private_key.pem"
        self._public_key_path = vapid_dir / "public_key.txt"
        self._public_key: str | None = None

    @property
    def channel_name(self) -> str:
        return "webpush"

    def ensure_vapid_keys(self) -> None:
        """Generate VAPID key pair if not already present. Idempotent."""
        if self._private_key_path.exists() and self._public_key_path.exists():
            os.chmod(self._private_key_path, 0o600)  # tighten keys written by older versions
            self._public_key = self._public_key_path.read_text().strip()
            logger.info("VAPID keys loaded from %s", self._vapid_dir)
            return

        from py_vapid import Vapid

        self._vapid_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        vapid = Vapid()
        vapid.generate_keys()
        # Written atomically and owner-only from the start: the signing key
        # for every push. (py_vapid's save_key() writes in place.)
        atomic_write_file(self._private_key_path, vapid.private_pem(), mode=0o600)

        # Save the public key as base64url-encoded raw uncompressed EC point,
        # which is the format browsers expect for applicationServerKey.
        # py_vapid's save_public_key() saves PEM format which doesn't work.
        raw_key = self._extract_public_key_base64url(vapid)
        atomic_write_file(self._public_key_path, raw_key.encode(), mode=0o644)
        self._public_key = raw_key
        logger.info("VAPID keys generated at %s", self._vapid_dir)

    @staticmethod
    def _extract_public_key_base64url(vapid: object) -> str:
        """Extract the raw uncompressed EC public key as a base64url string.

        The browser Push API requires the applicationServerKey as the raw
        65-byte uncompressed point (0x04 || x || y), base64url-encoded.
        """
        import base64

        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        pub_key: EllipticCurvePublicKey = vapid.public_key  # type: ignore[attr-defined]
        raw_bytes = pub_key.public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")

    def get_public_key(self) -> str:
        """Return the public VAPID key for browser subscriptions (base64url raw EC point)."""
        if self._public_key is None:
            self.ensure_vapid_keys()
        assert self._public_key is not None

        # If the stored key is in PEM format (from an older generation), convert it
        if self._public_key.startswith("-----"):
            self._public_key = self._convert_pem_to_base64url(self._public_key)
            # Overwrite the file with the correct format
            atomic_write_file(self._public_key_path, self._public_key.encode(), mode=0o644)
            logger.info("Converted VAPID public key from PEM to base64url format")

        return self._public_key

    @staticmethod
    def _convert_pem_to_base64url(pem_text: str) -> str:
        """Convert a PEM-encoded EC public key to base64url raw uncompressed point."""
        import base64

        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
            load_pem_public_key,
        )

        pub_key = load_pem_public_key(pem_text.encode("utf-8"))
        raw_bytes = pub_key.public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")

    async def send(self, event: NotificationEvent) -> None:
        """Send push notification to all stored subscriptions.

        The subscriptions are sent to concurrently. Raises unless at least
        one of them accepted the message, so a channel whose every push
        failed is never reported as delivered.
        """
        async with self._db_session_factory() as session:
            result = await session.execute(select(PushSubscription))
            subscriptions = result.scalars().all()

        if not subscriptions:
            raise RuntimeError("No push subscriptions registered")

        payload = json.dumps({
            "title": event.title,
            "body": event.body,
            "event_type": event.event_type.value,
        })

        outcomes = await asyncio.gather(*(self._send_one(sub, payload) for sub in subscriptions))
        expired_ids = [sub.id for sub, outcome in zip(subscriptions, outcomes, strict=True) if outcome == "expired"]

        # Remove expired subscriptions
        if expired_ids:
            async with self._db_session_factory() as session:
                await session.execute(
                    delete(PushSubscription).where(PushSubscription.id.in_(expired_ids))
                )
                await session.commit()

        if "sent" not in outcomes:
            if len(expired_ids) == len(subscriptions):
                raise RuntimeError(f"All {len(subscriptions)} push subscription(s) have expired")
            raise RuntimeError(f"Web Push delivery failed for all {len(subscriptions)} subscription(s)")

    async def _send_one(self, sub: PushSubscription, payload: str) -> str:
        """Push to one subscription: "sent", "expired" (410) or "failed"."""
        from pywebpush import WebPushException, webpush

        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh_key,
                "auth": sub.auth_key,
            },
        }
        try:
            # pywebpush is synchronous (requests): run it in a thread, with
            # a request timeout and an outer bound, so a hung push endpoint
            # never blocks the event loop, the API or the schedulers.
            await asyncio.wait_for(
                asyncio.to_thread(
                    webpush,
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=str(self._private_key_path),
                    vapid_claims={"sub": "mailto:omnisync@localhost"},
                    timeout=PUSH_TIMEOUT,
                ),
                timeout=PUSH_TIMEOUT + 5,
            )
            return "sent"
        except asyncio.TimeoutError:
            logger.warning("WebPush delivery to %s timed out after %ss", sub.endpoint[:60], PUSH_TIMEOUT)
        except WebPushException as exc:
            if hasattr(exc, "response") and exc.response is not None and exc.response.status_code == 410:
                logger.info("WebPush subscription expired (410), removing: %s", sub.endpoint[:60])
                return "expired"
            logger.warning("WebPush delivery failed for %s: %s", sub.endpoint[:60], exc)
        except Exception as exc:  # e.g. a malformed stored key
            logger.warning("WebPush delivery failed for %s: %s", sub.endpoint[:60], exc)
        return "failed"

    async def subscription_count(self) -> int:
        async with self._db_session_factory() as session:
            return int((await session.execute(select(func.count(PushSubscription.id)))).scalar() or 0)

    async def missing_dependencies(self) -> list[str]:
        """"vapid_keys" (none could be created) or "push_subscription" (no browser subscribed)."""
        if not self._private_key_path.exists():
            return ["vapid_keys"]
        if await self.subscription_count() == 0:
            return ["push_subscription"]
        return []

    async def is_available(self) -> bool:
        """Web Push can deliver once VAPID keys exist and a browser has subscribed."""
        return not await self.missing_dependencies()

    async def describe(self) -> dict[str, object]:
        missing = await self.missing_dependencies()
        count = await self.subscription_count() if self._private_key_path.exists() else 0
        return {"available": not missing, "missing_dependencies": missing, "subscriptions": count}

    async def add_subscription(self, endpoint: str, p256dh: str, auth: str) -> bool:
        """Store a browser push subscription; True if new, False if updated.

        Idempotent: a browser re-sending its subscription (after the channel
        was turned off and on, or when the page reconciles it) updates the
        stored keys instead of failing.
        """
        async with self._db_session_factory() as session:
            existing = (await session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )).scalar_one_or_none()
            if existing is not None:
                existing.p256dh_key = p256dh
                existing.auth_key = auth
                await session.commit()
                return False

            sub = PushSubscription(
                endpoint=endpoint,
                p256dh_key=p256dh,
                auth_key=auth,
                created_at=datetime.now(timezone.utc),
            )
            session.add(sub)
            await session.commit()
            return True

    async def remove_subscription(self, endpoint: str) -> bool:
        """Remove a push subscription. Returns True if found and deleted."""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            sub = result.scalar_one_or_none()
            if sub is None:
                return False
            await session.delete(sub)
            await session.commit()
            return True
