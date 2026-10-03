"""Custom exception hierarchy for OmniSync."""


class OmniSyncError(Exception):
    """Base exception for all OmniSync errors."""


class RcloneError(OmniSyncError):
    """Raised when an rclone subprocess fails."""


class RcloneAuthError(RcloneError):
    """Raised when rclone fails due to authentication issues."""


class ConfigError(OmniSyncError):
    """Raised when configuration is invalid or cannot be read/written."""


class WizardSessionError(OmniSyncError):
    """Raised when a wizard session operation fails (e.g. max sessions exceeded, session not found)."""


class NoCachedDiffError(OmniSyncError):
    """Raised when selective sync is attempted without a prior diff."""


class InvalidFilePathsError(OmniSyncError):
    """Raised when selective sync request contains paths not in the diff."""

    def __init__(self, invalid_paths: list[str]):
        self.invalid_paths = invalid_paths
        super().__init__(f"Invalid file paths: {invalid_paths}")


class SyncBusyError(OmniSyncError):
    """Raised when a sync is started while another operation holds the profile's sync lock."""

    def __init__(self, message: str | None = None):
        super().__init__(message or "A sync or another operation is already running for this profile. Wait for it to finish.")


class IntervalsNotResumableError(OmniSyncError):
    """Raised when resume is attempted while unresolved differences remain."""

    def __init__(self, pending_count: int, message: str | None = None):
        self.pending_count = pending_count
        super().__init__(message or f"Cannot resume: {pending_count} unresolved differences remain.")


class ProfileNotFoundError(OmniSyncError):
    """Raised when a sync profile is not found by slug."""

    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(f"Profile not found: {slug}")


class ProfileConflictError(OmniSyncError):
    """Raised when a profile's folder overlaps another profile's folder or a backup target.

    Overlap means the same folder or one inside the other.
    """

    def __init__(
        self,
        local_dir: str,
        conflicting_slug: str,
        field: str = "local_dir",
        other: str | None = None,
    ):
        self.local_dir = local_dir
        self.path = local_dir
        self.field = field
        self.conflicting_slug = conflicting_slug
        self.other = other
        if other is None:
            message = f"{field} '{local_dir}' already used by profile '{conflicting_slug}'"
        else:
            message = (
                f"{field} '{local_dir}' overlaps {other} of profile '{conflicting_slug}'; "
                "folders of different profiles and backup targets may not be the same "
                "folder or inside one another"
            )
        super().__init__(message)


class RcloneRateLimitError(RcloneError):
    """Raised when the provider rate-limited rclone (e.g. Drive 403 rateLimitExceeded, HTTP 429).

    Not an authentication problem: the same request succeeds after a pause,
    so callers retry with a longer backoff instead of asking the user to
    re-authorise.
    """


class ConflictResolutionError(OmniSyncError):
    """A conflict cannot be resolved as asked right now (the message says why)."""
