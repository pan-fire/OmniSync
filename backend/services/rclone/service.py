"""RcloneService: the rclone wrapper, assembled from its mixins."""

from __future__ import annotations

from backend.services.rclone.auth import AuthMixin
from backend.services.rclone.check import CheckMixin
from backend.services.rclone.config import ConfigMixin
from backend.services.rclone.listing import ListingMixin
from backend.services.rclone.probe import ProbeMixin
from backend.services.rclone.sync import SyncMixin


class RcloneService(SyncMixin, CheckMixin, ListingMixin, ConfigMixin, AuthMixin, ProbeMixin):
    """Wrapper around the rclone binary for sync operations.

    All rclone commands use an isolated config file (--config flag) to avoid
    touching the host user's rclone configuration. In Docker this defaults to
    /data/omnisync/rclone.conf; for local dev use OMNISYNC_RCLONE_CONFIG env var.
    """
