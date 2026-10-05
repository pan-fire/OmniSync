"""Pydantic request/response models for the OmniSync API."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from backend.services.path_guard import check_in_browse_roots, check_not_data_dir

# Size limits on caller-supplied lists and strings, so one request cannot
# store or hand rclone megabytes of arguments. The body itself is capped by
# security.BodySizeLimit.
MAX_RCLONE_ARGS = 64
MAX_RCLONE_FILTER_RULES = 500
MAX_ARG_LENGTH = 1024
MAX_PATH_LENGTH = 1024
MAX_SELECTIVE_ITEMS = 10000
MAX_FILE_PATH_LENGTH = 4096

RcloneArg = Annotated[str, Field(max_length=MAX_ARG_LENGTH)]
RcloneArgs = Annotated[list[RcloneArg], Field(max_length=MAX_RCLONE_ARGS)]
RcloneFilter = Annotated[list[RcloneArg], Field(max_length=MAX_RCLONE_FILTER_RULES)]

# Logging levels the global log_level setting accepts; applied to the
# "backend" logger (backend.services.config.apply_log_level).
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ErrorResponse(BaseModel):
    """The body of every error answer (4xx and 5xx); docs/api-errors.md lists the codes."""
    detail: str = Field(description="What went wrong, for people. Show this text.")
    code: str = Field(description="Stable machine-readable code (snake_case) to branch on.")
    details: dict[str, Any] | None = Field(
        None, description="Extra data for some codes, e.g. errors, invalid_paths, names, replacement, retry_after.",
    )
    request_id: str | None = Field(
        default=None, description="The request's id (also the X-Request-ID header); the server log lines of the request carry it.",
    )


class SyncDirection(str, Enum):
    """What POST /profiles/{slug}/sync/start runs.

    push / pull are one-way mirrors (rclone sync) and exist in both modes;
    two_way is a two-way sync (rclone bisync) and only for two_way profiles.
    """
    PUSH = "push"
    PULL = "pull"
    TWO_WAY = "two_way"


class SyncMode(str, Enum):
    """How a profile syncs automatically.

    two_way: the watcher, the interval and "Sync now" run rclone bisync,
    which carries changes both ways and keeps both versions of a file
    changed on both sides. mirror: local changes are pushed (rclone sync
    local -> remote) and the remote is pulled on the interval (remote ->
    local); the side that syncs last wins.
    """
    TWO_WAY = "two_way"
    MIRROR = "mirror"


class SyncState(str, Enum):
    IDLE = "idle"
    PUSHING = "pushing"
    PULLING = "pulling"
    SYNCING = "syncing"  # a two-way sync (or resync) is running
    ERROR = "error"


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProgressFileResponse(BaseModel):
    """A file a running sync is transferring right now."""
    name: str
    size: int | None = None
    bytes: int = 0
    percentage: int | None = None


class SyncProgress(BaseModel):
    """Live progress of a running push, pull or two-way sync (rclone's stats, about every second).

    Totals grow while rclone is still listing, so a percentage computed
    from them may go down. ``speed`` is in bytes per second; ``eta_seconds``
    is None while rclone cannot estimate it. ``current_files`` holds at most
    5 of the files in flight.
    """
    bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    eta_seconds: int | None = None
    files_done: int = 0
    files_total: int = 0
    checks: int = 0
    total_checks: int = 0
    current_files: list[ProgressFileResponse] = []


class SyncStatusResponse(BaseModel):
    state: SyncState
    last_sync: datetime | None = None
    current_job_id: int | None = None
    files_processed: int = 0
    errors: int = 0
    pending_changes: int = 0  # number of unsynced files detected at startup
    intervals_paused: bool = False
    paused_at: datetime | None = None
    last_error: str | None = None  # why the last sync failed or was refused
    # A two-way profile whose sync state is lost or inconsistent: automatic
    # syncing is paused until the user confirms a resync (last_error says why).
    resync_required: bool = False
    # Paused by the user (Pause / Pause all): intervals_paused is then true
    # as well. Only the user's Resume lifts it, never a successful sync.
    user_paused: bool = False
    progress: SyncProgress | None = None  # while a sync runs
    # The profile has a sync window and it is closed now: automatic syncs
    # wait until next_window_start; waiting_for_window means one is due then.
    outside_sync_window: bool = False
    next_window_start: datetime | None = None
    waiting_for_window: bool = False


class SyncStartRequest(BaseModel):
    direction: SyncDirection
    force: bool = False


class ResyncRequest(BaseModel):
    """POST /profiles/{slug}/sync/resync. confirm must be true."""
    confirm: bool = False


class SyncStartResponse(BaseModel):
    """A sync start: 202 while the job runs, 200 when it ended before changing anything.

    Clients follow a running job with GET /jobs/{job_id} or the profile's
    status. ``error`` is the refusal (the job's error and last_error) of a
    run that ended at once.
    """
    job_id: int
    state: SyncState
    error: str | None = None
    # E.g. that the run started outside the profile's sync window.
    note: str | None = None


class SyncStopResponse(BaseModel):
    state: SyncState
    message: str


class JobDirection(str, Enum):
    """What a recorded job did; per-file actions are 'selective'."""

    PUSH = "push"
    PULL = "pull"
    SELECTIVE = "selective"
    TWO_WAY = "two_way"  # a two-way sync (rclone bisync)
    RESYNC = "resync"  # a two-way resync: the union of both sides, nothing deleted


class SyncWarningCode(str, Enum):
    """Why a sync left a file alone, or did something the user should know about.

    name_collision: names in one local folder equal after Unicode
    normalisation (e.g. ``café`` composed and decomposed); rclone treats
    them as one file and syncs only one. name_not_utf8: a local name that
    is not valid UTF-8; whole-folder syncs carry it, per-file actions
    cannot. symlink_shadow (preview, diff): a local symbolic link has the
    name of a remote file or folder. symlink_kept (pull, two-way): that
    remote item was left alone so the link is not replaced. symlink_trashed
    (push): that remote item was moved to the remote trash.
    """
    NAME_COLLISION = "name_collision"
    NAME_NOT_UTF8 = "name_not_utf8"
    SYMLINK_SHADOW = "symlink_shadow"
    SYMLINK_KEPT = "symlink_kept"
    SYMLINK_TRASHED = "symlink_trashed"


class SyncWarning(BaseModel):
    """One kind of name problem: how many local names have it, and some of them.

    ``paths`` holds at most 20 of the ``count`` paths, escaped for display:
    bytes that are not UTF-8 as ``\\xNN``, control and bidi characters as
    ``\\xNN`` or ``\\uNNNN``; a folder ends with ``/``.
    """
    code: SyncWarningCode
    count: int
    paths: list[str] = []


class SyncJobResponse(BaseModel):
    id: int
    direction: JobDirection
    started_at: datetime
    finished_at: datetime | None = None
    status: JobStatus
    files_changed: int = 0
    conflicts: int = 0
    errors: int = 0
    profile_slug: str | None = None
    profile_name: str | None = None
    # Files the job left alone or had to treat specially (see SyncWarningCode);
    # a "completed" job with warnings did not sync everything.
    warnings: list[SyncWarning] = []


class FileChangeAction(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class FileSide(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class FileChangeResponse(BaseModel):
    id: int
    job_id: int
    file_path: str
    action: FileChangeAction
    size_bytes: int | None = None
    # The folder that changed; None for changes recorded before it was known.
    side: FileSide | None = None


class ConflictResolution(str, Enum):
    """How a conflict is resolved.

    keep_local copies the local file over the remote one, keep_remote the
    other way round (the replaced file goes to .omnisync-trash), keep_both
    keeps the remote version next to the local one as
    <name>.conflict-<timestamp> on both sides. dismiss only closes the
    record and changes no file.
    """
    KEEP_LOCAL = "keep_local"
    KEEP_REMOTE = "keep_remote"
    KEEP_BOTH = "keep_both"
    DISMISS = "dismiss"


class ConflictResponse(BaseModel):
    id: int
    # None for a conflict a diff found (no sync job involved).
    job_id: int | None = None
    file_path: str
    local_modified: datetime | None = None
    remote_modified: datetime | None = None
    resolved: bool = False
    # None on a resolved conflict means the two files no longer differed.
    resolution: ConflictResolution | None = None
    profile_slug: str | None = None
    profile_name: str | None = None
    # Set for a conflict a two-way sync found: both versions were kept, on
    # both sides, under these names (one of them is file_path, unless neither
    # version was newer). keep_local / keep_remote keep only that version
    # under file_path on both sides (the other goes to .omnisync-trash); keep_both and
    # dismiss close the record and leave both files.
    local_kept_as: str | None = None
    remote_kept_as: str | None = None


class ConflictResolveRequest(BaseModel):
    resolution: ConflictResolution


class RemoteResponse(BaseModel):
    name: str
    type: str
    last_verified: datetime | None = None
    # What OmniSync can do with a remote of this type (see
    # provider_registry.remote_capabilities): the wizard provider it was made
    # with (None for a type the wizard does not offer), whether its settings
    # can be edited (PUT /remotes/{name}) and whether it signs in with OAuth
    # and can be reconnected (POST /wizard/authorize with remote_name).
    provider_id: str | None = None
    editable: bool = False
    reconnectable: bool = False
    # The last connection test, health check or sync of this remote failed
    # because the provider refused the credentials (since the backend started).
    auth_error: bool = False


class LogEntryResponse(BaseModel):
    """One entry of the server log, newest first in GET /logs."""

    timestamp: datetime
    level: str
    message: str
    logger: str | None = Field(default=None, description="The logger that wrote it, e.g. backend.audit for user actions.")
    request_id: str | None = Field(default=None, description="The id of the API request it was written in, if any.")
    exc: str | None = Field(default=None, description="The traceback or further lines that belong to the entry.")


class HealthResponse(BaseModel):
    """GET /health: local checks only, cheap enough for a container health check.

    ``status`` is "ok" when the database answers and the rclone binary is on
    PATH, otherwise "degraded" (served with HTTP 503). Remote reachability
    needs provider calls and lives at GET /health/remotes.
    """

    status: str
    rclone_installed: bool
    uptime_seconds: float
    database_ok: bool
    # The release version (the repository's VERSION file). Public on purpose:
    # it carries no secret and lets clients show what they are talking to.
    version: str


class RemoteHealth(BaseModel):
    remote: str
    accessible: bool
    profiles: list[str] = []
    # Not accessible because the provider refused the credentials.
    auth_error: bool = False


class RemoteHealthResponse(BaseModel):
    """GET /health/remotes: reachability of every remote a running profile uses."""

    remotes: list[RemoteHealth] = []


# --- Wizard schemas ---


class AuthType(str, Enum):
    KEY = "key"
    OAUTH = "oauth"


class FieldType(str, Enum):
    TEXT = "text"
    # A secret: masked in forms, never returned by GET /remotes/{name}/config.
    PASSWORD = "password"
    # One of ProviderFieldResponse.options.
    SELECT = "select"
    # "<existing remote>:<path>", e.g. the remote a crypt remote encrypts into.
    REMOTE_PATH = "remote_path"


class WizardSessionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ProviderFieldResponse(BaseModel):
    name: str
    label: str
    field_type: FieldType
    required: bool
    help_text: str = ""
    # For field_type "select": the allowed values.
    options: list[str] = []
    # The value a new remote starts with ("" for none).
    default: str = ""


class ProviderResponse(BaseModel):
    id: str
    display_name: str
    icon: str
    auth_type: AuthType
    fields: list[ProviderFieldResponse]
    default_name: str
    setup_guide: str = ""


class AuthorizeRequest(BaseModel):
    provider_id: str
    # The user's own OAuth app. OmniSync ships no app of its own: a new
    # remote needs client_id, and client_secret where the provider's token
    # endpoint needs one (Google).
    client_id: str | None = None
    client_secret: str | None = None
    # Reconnect: sign an existing OAuth remote in again. The finished
    # session is then passed to POST /wizard/reconnect, which replaces only
    # the remote's token. Without client_id the remote's own app
    # credentials from rclone.conf are used.
    remote_name: str | None = Field(default=None, max_length=255)


class AuthorizeResponse(BaseModel):
    session_id: str
    auth_url: str
    # The address the provider sends the browser back to after consent.
    redirect_uri: str = ""


class OAuthRedirectResponse(BaseModel):
    """GET /wizard/oauth/redirect-uri: the redirect URI to register with the
    user's own OAuth app, as the backend would use it for this client."""

    redirect_uri: str


class WizardSessionResponse(BaseModel):
    # The OAuth token never leaves the backend; POST /wizard/create picks it
    # up from the session by session_id.
    session_id: str
    status: WizardSessionStatus
    auth_url: str | None = None
    error: str | None = None
    # Set with some failures, for a translated explanation:
    # onedrive_drive_lookup_failed.
    error_code: str | None = None


class CreateRemoteRequest(BaseModel):
    """POST /wizard/create. OAuth providers pass the session_id of a completed
    authorization instead of a token; unknown fields (such as a token) are rejected."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=255)
    provider_id: str = Field(max_length=64)
    # Values may be whole service-account JSON files, so they get room.
    params: Annotated[
        dict[Annotated[str, Field(max_length=128)], Annotated[str, Field(max_length=65536)]],
        Field(max_length=64),
    ] = {}
    session_id: str | None = Field(default=None, max_length=128)


def check_remote_name(value: str) -> str:
    """An rclone remote name as the wizard creates them; never an option."""
    if not REMOTE_PATH_PATTERN.fullmatch(f"{value}:"):
        raise ValueError("name must be an rclone remote name (letters, digits, '_' and '-', not starting with '-')")
    return value


class ReconnectRemoteRequest(BaseModel):
    """POST /wizard/reconnect: store the token of a completed reconnect
    authorization in the remote it was started for."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=255)
    session_id: str = Field(max_length=128)


_ParamKey = Annotated[str, Field(max_length=128)]
_ParamValue = Annotated[str, Field(max_length=65536)]


class RemoteConfigField(BaseModel):
    """One provider setting of an existing remote. A secret's value is never
    returned: ``value`` is then empty and ``is_set`` says whether one is stored."""

    name: str
    value: str = ""
    is_set: bool = False
    secret: bool = False


class RemoteConfigResponse(BaseModel):
    """GET /remotes/{name}/config: the remote's settings as the wizard knows
    them, secrets masked. OAuth tokens are never part of it."""

    name: str
    type: str
    provider_id: str
    fields: list[RemoteConfigField] = []
    # Settings in rclone.conf that the wizard has no field for (names only);
    # an update keeps them as they are.
    other_keys: list[str] = []


class UpdateRemoteRequest(BaseModel):
    """PUT /remotes/{name}: change a remote's settings in place.

    ``params`` holds the provider fields to change. A non-secret field set to
    "" is removed (rclone then uses its default); a secret field left out or
    set to "" keeps the stored secret, any other value replaces it. Secrets
    named in ``clear`` are removed (e.g. an SFTP password, to use a key)."""

    model_config = ConfigDict(extra="forbid")

    params: Annotated[dict[_ParamKey, _ParamValue], Field(max_length=64)] = {}
    clear: Annotated[list[_ParamKey], Field(max_length=64)] = []


# rclone.conf text the import accepts; the API's whole request body is 1 MB.
MAX_IMPORT_CONFIG_LENGTH = 512 * 1024


class ImportConfigRequest(BaseModel):
    """POST /remotes/import/preview: an rclone.conf to look at."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=MAX_IMPORT_CONFIG_LENGTH)


class ImportRemoteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The section name in the uploaded file.
    source: str = Field(max_length=255)
    # The name to add it under; defaults to ``source``.
    name: str | None = Field(default=None, max_length=255)


class ImportRemotesRequest(BaseModel):
    """POST /remotes/import: add the selected sections of an rclone.conf."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=MAX_IMPORT_CONFIG_LENGTH)
    remotes: Annotated[list[ImportRemoteSelection], Field(min_length=1, max_length=256)]


class ImportCandidate(BaseModel):
    """One section of an uploaded rclone.conf. Values are never returned."""

    name: str
    type: str
    # A remote of this name exists already: import it under another name.
    exists: bool = False
    # Reasons it cannot be imported (an invalid name, a refused setting).
    problems: list[str] = []
    keys: list[str] = []


class ImportPreviewResponse(BaseModel):
    remotes: list[ImportCandidate] = []
    # Problems with the file as a whole (it cannot be parsed).
    errors: list[str] = []


class ImportRemotesResponse(BaseModel):
    imported: list[str] = []


class TestRemoteRequest(BaseModel):
    name: str = Field(max_length=255)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return check_remote_name(v)


class TestRemoteResponse(BaseModel):
    success: bool
    error: str | None = None
    # The provider refused the credentials: reconnect (OAuth) or edit them.
    auth_error: bool = False


class TestSyncRequest(BaseModel):
    """POST /config/test-sync: the same checks as a new profile's folders."""
    local_dir: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    remote_dir: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)

    @field_validator("local_dir")
    @classmethod
    def _local_dir(cls, v: str) -> str:
        return check_in_browse_roots(check_local_dir(v), "local_dir")

    @field_validator("remote_dir")
    @classmethod
    def _remote_dir(cls, v: str) -> str:
        return check_remote_dir(v)


class TestSyncResponse(BaseModel):
    success: bool
    steps: list[dict[str, object]] = []
    error: str | None = None


# --- Browse schemas ---


class DirEntry(BaseModel):
    name: str
    path: str


class BrowseResponse(BaseModel):
    current: str
    parent: str | None = None
    entries: list[DirEntry] = []


# --- Sync check schemas ---


class SyncCheckResponse(BaseModel):
    """Result of comparing local vs remote directories."""
    has_changes: bool = False
    local_only: list[str] = []
    remote_only: list[str] = []
    differ: list[str] = []
    error: str | None = None


class SyncPreviewCounts(BaseModel):
    """What one bulk sync direction would do to its destination."""
    deletes: int = 0  # files only on the destination: deleted (moved to the trash)
    replaces: int = 0  # files on both sides that differ: overwritten (old version to the trash)
    creates: int = 0  # files only on the source: copied
    exceeds_max_delete: bool = False  # the sync would stop at the delete limit


class TwoWayPreview(BaseModel):
    """What the next two-way sync of a two_way profile would do (rclone bisync --dry-run).

    ``local`` / ``remote`` count the changes to each folder; deletes and
    replaced files go to that side's .omnisync-trash. ``exceeds_max_delete``
    is set on a side that would lose more files than the delete limit: the
    sync then stops before changing anything. ``conflicts`` counts files
    changed on both sides (both versions are kept). ``resync`` is true when
    the next run is a resync (first run, changed filters): the union of both
    sides, nothing deleted. ``resync_required`` means automatic syncing is
    paused until the user confirms a resync.
    """
    local: SyncPreviewCounts = SyncPreviewCounts()
    remote: SyncPreviewCounts = SyncPreviewCounts()
    conflicts: int = 0
    resync: bool = False
    resync_required: bool = False
    error: str | None = None


class SyncPreviewResponse(BaseModel):
    """POST /profiles/{slug}/sync/preview: a push and a pull, counted, nothing changed.

    Files a bulk sync leaves out (manually flagged, unresolved conflicts)
    are not counted but reported in ``excluded``. ``max_delete`` is the
    profile's effective delete limit (None: no limit). For a two_way
    profile ``two_way`` previews the next two-way sync.
    """
    push: SyncPreviewCounts = SyncPreviewCounts()
    pull: SyncPreviewCounts = SyncPreviewCounts()
    excluded: int = 0
    max_delete: int | None = None
    error: str | None = None
    sync_mode: SyncMode = SyncMode.MIRROR
    two_way: TwoWayPreview | None = None
    # Local names a sync cannot carry as they are (see SyncWarning).
    warnings: list[SyncWarning] = []


# --- Granular sync control schemas ---


class ChangeCategory(str, Enum):
    LOCAL_ONLY = "local_only"
    REMOTE_ONLY = "remote_only"
    MODIFIED_LOCAL = "modified_local"
    MODIFIED_REMOTE = "modified_remote"
    MODIFIED_BOTH = "modified_both"


class FileAction(str, Enum):
    PUSH = "push"
    PULL = "pull"
    SKIP = "skip"
    MANUAL = "manual"
    KEEP_BOTH = "keep_both"


class FileDiff(BaseModel):
    """A single file difference with metadata."""
    path: str
    category: ChangeCategory
    local_size: int | None = None
    remote_size: int | None = None
    local_mod_time: datetime | None = None
    remote_mod_time: datetime | None = None
    is_conflict: bool = False
    manual_flag: bool = False


class DiffSummary(BaseModel):
    """Count of files per change category."""
    local_only: int = 0
    remote_only: int = 0
    modified_local: int = 0
    modified_remote: int = 0
    modified_both: int = 0
    manual: int = 0
    total: int = 0


class DiffPagination(BaseModel):
    """Pagination metadata for paginated diff responses."""
    offset: int = 0
    limit: int = 0
    total: int = 0
    has_more: bool = False


class DiffResponse(BaseModel):
    """Enhanced diff result with per-file metadata."""
    files: list[FileDiff] = []
    summary: DiffSummary = DiffSummary()
    pagination: DiffPagination | None = None
    error: str | None = None
    # Local names a sync cannot carry as they are (see SyncWarning).
    warnings: list[SyncWarning] = []


class SelectiveSyncItem(BaseModel):
    """A single file action in a selective sync request."""
    path: str = Field(max_length=MAX_FILE_PATH_LENGTH)
    action: FileAction


class SelectiveSyncRequest(BaseModel):
    """Request body for POST /sync/selective."""
    items: list[SelectiveSyncItem] = Field(max_length=MAX_SELECTIVE_ITEMS)


class FileError(BaseModel):
    """An error for a specific file during selective sync."""
    path: str
    error: str


class SelectiveSyncResponse(BaseModel):
    """Per-file actions: running (POST answers 202 at once), then their result.

    Follow a running one with GET /profiles/{slug}/sync/selective/{job_id}
    (or GET /jobs/{job_id}). ``errors`` lists each file that failed and why.
    """
    job_id: int
    status: JobStatus = JobStatus.COMPLETED
    total: int
    succeeded: int
    failed: int
    errors: list[FileError] = []


class ManualFlagsResponse(BaseModel):
    """List of files with active manual flags."""
    flags: list[str] = []


class ResumeIntervalsResponse(BaseModel):
    """Response from POST /sync/resume-intervals."""
    detail: str


# --- Notification Schemas ---


# The settings of the webhook, ntfy and email channels as the API shows
# them: a secret (header value, token, password) is never returned, only
# whether one is stored (``*_set``).


class WebhookHeaderView(BaseModel):
    name: str
    value_set: bool


class WebhookSettingsView(BaseModel):
    url: str = ""
    allow_http: bool = False
    headers: list[WebhookHeaderView] = []


class NtfySettingsView(BaseModel):
    server: str = ""
    topic: str = ""
    allow_http: bool = False
    username: str = ""
    token_set: bool = False
    password_set: bool = False


class EmailSettingsView(BaseModel):
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password_set: bool = False
    from_addr: str = ""
    to: list[str] = []


class ChannelConfig(BaseModel):
    enabled: bool = True
    min_severity: str = Field(default="warning", pattern=r"^(debug|info|warning|error)$")
    # Set for the channel of that name only.
    webhook: WebhookSettingsView | None = None
    ntfy: NtfySettingsView | None = None
    email: EmailSettingsView | None = None


class NotificationConfigResponse(BaseModel):
    channels: dict[str, ChannelConfig]


# Updates. A secret left out or empty keeps the stored one; ``clear``
# removes it. The webhook's header list replaces the stored one, and a
# header given with an empty value keeps the stored value of that name.


class WebhookHeaderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    value: str = Field(default="", max_length=4096)


class WebhookSettingsUpdate(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    allow_http: bool | None = None
    headers: list[WebhookHeaderUpdate] | None = Field(default=None, max_length=10)


class NtfySettingsUpdate(BaseModel):
    server: str | None = Field(default=None, max_length=2048)
    topic: str | None = Field(default=None, max_length=64)
    allow_http: bool | None = None
    username: str | None = Field(default=None, max_length=256)
    token: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=512)
    clear: list[Literal["token", "password"]] = []


class EmailSettingsUpdate(BaseModel):
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] | None = None
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    from_addr: str | None = Field(default=None, max_length=320)
    to: list[Annotated[str, Field(max_length=320)]] | None = Field(default=None, max_length=20)
    clear: list[Literal["password"]] = []


class ChannelConfigUpdate(BaseModel):
    """A partial channel update: only the fields given are changed."""
    enabled: bool | None = None
    min_severity: str | None = Field(default=None, pattern=r"^(debug|info|warning|error)$")
    # The settings of the channel of that name (others are refused).
    webhook: WebhookSettingsUpdate | None = None
    ntfy: NtfySettingsUpdate | None = None
    email: EmailSettingsUpdate | None = None


class NotificationConfigUpdateRequest(BaseModel):
    channels: Annotated[
        dict[Annotated[str, Field(max_length=64)], ChannelConfigUpdate], Field(max_length=32),
    ] | None = None


class ChannelStatusInfo(BaseModel):
    available: bool
    detection_method: str | None = None
    host_os: str | None = None
    # Codes of what keeps the channel from delivering, e.g. "notify-send",
    # "dbus_socket", "push_subscription" (empty when available).
    missing_dependencies: list[str] = []
    permission_status: str | None = None
    subscriptions: int | None = None  # Web Push: browsers subscribed


class ChannelStatusResponse(BaseModel):
    channels: dict[str, ChannelStatusInfo]
    config_error: bool = False  # the notification settings in config.toml have errors
    unknown_channels: list[str] = []  # configured names no channel handles


# Browser push services a subscription endpoint may point at. The server
# POSTs to the endpoint on every notification, so an arbitrary URL would let a
# caller make it send requests into the local network. OMNISYNC_PUSH_HOSTS
# (comma-separated host suffixes) adds more.
PUSH_SERVICE_HOST_SUFFIXES = (
    "fcm.googleapis.com",            # Chrome, Edge (Chromium), Opera
    "push.services.mozilla.com",     # Firefox
    "push.apple.com",                # Safari (web.push.apple.com)
    "notify.windows.com",            # legacy Edge (*.notify.windows.com)
)


def check_push_endpoint(url: str) -> str:
    import os
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    extra = tuple(h.strip().lower() for h in os.environ.get("OMNISYNC_PUSH_HOSTS", "").split(",") if h.strip())
    allowed = PUSH_SERVICE_HOST_SUFFIXES + extra
    if parts.scheme != "https" or not any(host == s or host.endswith("." + s) for s in allowed):
        raise ValueError("endpoint must be an https URL of a browser push service")
    return url


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(max_length=2048)
    # A browser sends two keys (p256dh, auth) of under 100 characters each.
    keys: Annotated[
        dict[Annotated[str, Field(max_length=32)], Annotated[str, Field(max_length=512)]],
        Field(max_length=8),
    ]

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v: str) -> str:
        return check_push_endpoint(v)


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(max_length=2048)


class VapidPublicKeyResponse(BaseModel):
    public_key: str


class NotificationLogEntry(BaseModel):
    id: int
    event_type: str
    severity: str
    title: str
    body: str
    timestamp: str
    channels_delivered: list[str]


class NotificationHistoryResponse(BaseModel):
    items: list[NotificationLogEntry]
    total: int


class TestNotificationRequest(BaseModel):
    channel: str | None = None


class TestNotificationResponse(BaseModel):
    success: bool
    channels_delivered: list[str]
    # Channel -> error code: "unavailable", "timeout" or "failed" (details are logged).
    errors: dict[str, str]



# --- rclone argument safety ---
# local_dir, remote_dir and rclone_args end up on rclone's command line. rclone
# reads any token starting with "-" as a flag, and some flags read or write
# arbitrary files or run commands (--config, --log-file, --password-command,
# --sftp-ssh), so these values are checked before they are stored.

REMOTE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*:")

# Flags a profile may pass to rclone: transfer tuning, filtering and limits.
# Anything that names a file, runs a command or changes logging is left out.
# Flags that take a value ("--transfers 4" or "--transfers=4"):
SAFE_RCLONE_VALUE_FLAGS = frozenset({
    "bwlimit", "transfers", "checkers", "tpslimit", "tpslimit-burst",
    "max-delete", "max-delete-size", "max-transfer", "max-size", "min-size",
    "max-age", "min-age", "max-depth", "exclude", "include", "filter", "exclude-if-present",
    "low-level-retries", "retries", "retries-sleep", "contimeout",
    "timeout", "multi-thread-streams", "buffer-size",
    "drive-export-formats", "drive-chunk-size",
    "onedrive-chunk-size", "s3-chunk-size", "s3-upload-concurrency", "b2-chunk-size",
})
# Switches ("--fast-list", or "--fast-list=true|false"); never followed by a value.
SAFE_RCLONE_BOOL_FLAGS = frozenset({
    "fast-list", "size-only", "checksum",
    "update", "ignore-existing", "ignore-size", "ignore-times", "ignore-case-sync",
    "no-update-modtime", "track-renames", "create-empty-src-dirs",
    "skip-links", "one-file-system", "drive-skip-gdocs",
    "drive-acknowledge-abuse", "drive-use-trash",
})
SAFE_RCLONE_FLAGS = SAFE_RCLONE_VALUE_FLAGS | SAFE_RCLONE_BOOL_FLAGS


def _no_control_chars(value: str, field: str) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise ValueError(f"{field} may not contain line breaks or NUL characters")
    return value


def check_local_dir(value: str) -> str:
    """An absolute path outside OmniSync's data directory.

    The OMNISYNC_BROWSE_ROOTS check (check_in_browse_roots) applies to new
    values only, so callers run it where they know the value is new.
    """
    _no_control_chars(value, "local_dir")
    if not value.startswith("/"):
        raise ValueError("local_dir must be an absolute path, e.g. /home/you/Sync")
    return check_not_data_dir(value, "local_dir")


def check_remote_dir(value: str) -> str:
    _no_control_chars(value, "remote_dir")
    if not REMOTE_PATH_PATTERN.match(value):
        raise ValueError("remote_dir must be '<remote>:<path>', e.g. gdrive:Backup")
    # (Imported here: the rclone package imports this module.)
    from backend.services.rclone.process import RESERVED_NAME_MESSAGE, is_reserved_remote_name

    if is_reserved_remote_name(value.split(":", 1)[0]):
        raise ValueError(f"remote_dir cannot use this remote: {RESERVED_NAME_MESSAGE}")
    return value


# A separate value may start with "-" only as a number ("--max-delete -1").
_NEGATIVE_NUMBER = re.compile(r"-[0-9]+(\.[0-9]+)?")


def check_rclone_args(args: list[str]) -> list[str]:
    """Allow-listed flags only, each value right after the flag that takes it.

    A switch (SAFE_RCLONE_BOOL_FLAGS) is never followed by a value: rclone
    would read that token as a source or destination before the "--" that
    precedes the profile's own paths.
    """
    expects_value: str | None = None  # the value flag waiting for its value
    for arg in args:
        _no_control_chars(arg, "rclone_args")
        if expects_value is not None:
            if not arg or (arg.startswith("-") and not _NEGATIVE_NUMBER.fullmatch(arg)):
                raise ValueError(f"rclone flag --{expects_value} needs a value, e.g. --{expects_value}=<value>")
            expects_value = None
            continue
        if not arg.startswith("--"):
            raise ValueError(f"'{arg}' is not a flag or the value of the flag before it")
        name, has_value, value = arg[2:].partition("=")
        if name in SAFE_RCLONE_VALUE_FLAGS:
            if not has_value:
                expects_value = name
            elif not value:
                raise ValueError(f"rclone flag --{name} needs a value")
        elif name in SAFE_RCLONE_BOOL_FLAGS:
            if has_value and value.lower() not in ("true", "false"):
                raise ValueError(f"rclone flag --{name} is a switch: give no value, or =true / =false")
        else:
            raise ValueError(f"rclone flag not allowed: {arg.split('=')[0]}")
    if expects_value is not None:
        raise ValueError(f"rclone flag --{expects_value} needs a value")
    return args


# Flags a two-way profile may not use: they can hide the sync marker
# (.omnisync-check) from bisync's --check-access, and every two-way sync
# would then fail. Path filters belong in the profile's filter rules, which
# the engine puts after a rule that always includes the marker. --max-size
# and --max-depth are allowed with values that keep the (tiny, top-level)
# marker visible.
TWO_WAY_HIDING_FLAGS = frozenset({
    "include", "exclude", "filter", "exclude-if-present", "max-age", "min-age", "min-size",
})
_SIZE_RE = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([kmgtpe]?)(i?b?)")


def _size_bytes(value: str) -> float | None:
    """An rclone size (the default unit is KiB, as rclone reads it); None if unreadable."""
    match = _SIZE_RE.fullmatch(value.strip().lower())
    if match is None or (match.group(3).startswith("i") and not match.group(2)):
        return None
    number, unit, tail = match.groups()
    if unit:
        return float(number) * 1024 ** ("kmgtpe".index(unit) + 1)
    return float(number) * (1 if tail == "b" else 1024)


def two_way_rclone_args_error(args: list[str]) -> str | None:
    """Why a two-way profile cannot use these rclone_args, or None."""
    for i, arg in enumerate(args):
        if not arg.startswith("--"):
            continue
        name, has_value, value = arg[2:].partition("=")
        if not has_value:
            value = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else ""
        if name in TWO_WAY_HIDING_FLAGS:
            hint = (
                " Use the profile's filter rules instead (e.g. '+ /Docs/**' then '- **')."
                if name in ("include", "exclude", "filter") else ""
            )
        elif name == "max-size" and value.strip().lower() not in ("off", "-1") and \
                ((size := _size_bytes(value)) is None or size < 1024):
            hint = " A limit of 1Ki or more is fine."
        elif name == "max-depth" and not (value.strip().isdigit() and int(value) >= 1):
            hint = " A depth of 1 or more is fine."
        else:
            continue
        return (
            f"rclone flag --{name} cannot be used by a two-way profile: it can hide the sync "
            f"marker .omnisync-check, and every two-way sync would then fail.{hint}"
        )
    return None


def check_rclone_filter(rules: list[str]) -> list[str]:
    for rule in rules:
        _no_control_chars(rule, "rclone_filter")
    return rules

# --- Bandwidth limit and sync window ---

# rclone's --bwlimit: a rate ("10M", "512k", "off", "10M:1M" for
# upload:download) or a timetable of "[Day-]HH:MM,rate" entries separated by
# spaces, e.g. "08:00,512k 19:00,10M 23:00,off" or "Mon-08:00,1M Sat-00:00,off".
# Verified against rclone 1.75.1 (sizes: b, k/ki/kib, M, G, T, P, E; any case).
MAX_BWLIMIT_LENGTH = 500
_BW_RATE = r"(?:off|(?:\d+(?:\.\d*)?|\.\d+)(?:b|[kmgtpe](?:ib?)?)?)"
_BW_RATES = rf"{_BW_RATE}(?::{_BW_RATE})?"
_BW_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_BW_ENTRY = rf"(?:{_BW_DAY}-)?(?:[01]\d|2[0-3]):[0-5]\d,{_BW_RATES}"
_BWLIMIT_RE = re.compile(rf"{_BW_RATES}|{_BW_ENTRY}(?: +{_BW_ENTRY})*", re.IGNORECASE)


def check_bwlimit(value: str | None) -> str | None:
    """A valid rclone --bwlimit value (spaces normalised), or None for none / an empty value."""
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > MAX_BWLIMIT_LENGTH or not _BWLIMIT_RE.fullmatch(value):
        raise ValueError(
            "bwlimit must be an rclone bandwidth limit: a rate such as 10M, 512k or off (upload:download "
            "as 10M:1M), or a timetable such as '08:00,512k 19:00,10M 23:00,off' (times HH:MM, "
            "optionally with a day: 'Mon-08:00,1M')"
        )
    return value


def bwlimit_conflict(bwlimit: str | None, rclone_args: list[str]) -> str | None:
    """Why the bandwidth limit field and these rclone_args cannot both be set, or None."""
    if bwlimit and any(a == "--bwlimit" or a.startswith("--bwlimit=") for a in rclone_args):
        return (
            "The bandwidth limit is set twice: in the Bandwidth limit field and as --bwlimit in the "
            "extra rclone flags. Remove one of them."
        )
    return None


_HHMM_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class SyncWindow(BaseModel):
    """When automatic syncs (file watcher, interval) may run, in the server's local time.

    ``days``: 0 = Monday ... 6 = Sunday, the days a window starts on.
    ``end`` before ``start`` spans midnight (22:00-06:00 runs into the
    next morning). Syncs the user starts run at any time.
    """
    days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start: str
    end: str

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v: str) -> str:
        if not _HHMM_RE.fullmatch(v):
            raise ValueError("times are HH:MM (24-hour), e.g. 22:00")
        return v

    @model_validator(mode="after")
    def _not_empty(self) -> "SyncWindow":
        if self.start == self.end:
            raise ValueError("the sync window must not start and end at the same time")
        return self


# --- Profile schemas ---

# Limits on profile settings; the web and TUI forms use the same numbers.
DEBOUNCE_SECONDS_MIN, DEBOUNCE_SECONDS_MAX = 1, 3600            # 1 s .. 1 h
PULL_INTERVAL_MINUTES_MIN, PULL_INTERVAL_MINUTES_MAX = 1, 10080  # 1 min .. 7 days
MAX_RETRIES_MIN, MAX_RETRIES_MAX = 1, 10


def profile_slug(name: str) -> str:
    """The URL slug of a profile name: lowercase ASCII letters, digits and dashes."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def check_profile_name(name: str) -> str:
    _no_control_chars(name, "name")
    if not profile_slug(name):
        raise ValueError("name must contain at least one letter or digit (a-z, 0-9)")
    return name


class ProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    local_dir: str = Field(min_length=1, max_length=1024)
    remote_dir: str = Field(min_length=1, max_length=1024)
    debounce_seconds: int = Field(default=5, ge=DEBOUNCE_SECONDS_MIN, le=DEBOUNCE_SECONDS_MAX)
    pull_interval_minutes: int = Field(
        default=5, ge=PULL_INTERVAL_MINUTES_MIN, le=PULL_INTERVAL_MINUTES_MAX,
    )
    # New profiles sync both ways; the first run is a resync (the union).
    # Declared before rclone_args, whose check depends on it.
    sync_mode: SyncMode = SyncMode.TWO_WAY
    rclone_filter: RcloneFilter = []
    rclone_args: RcloneArgs = []
    max_retries: int = Field(default=3, ge=MAX_RETRIES_MIN, le=MAX_RETRIES_MAX)
    # rclone --bwlimit for this profile's syncs (see check_bwlimit).
    bwlimit: str | None = Field(default=None, max_length=MAX_BWLIMIT_LENGTH)
    sync_window: SyncWindow | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return check_profile_name(v)

    @field_validator("bwlimit")
    @classmethod
    def _bwlimit(cls, v: str | None) -> str | None:
        return check_bwlimit(v)

    @model_validator(mode="after")
    def _one_bwlimit(self) -> "ProfileCreateRequest":
        if error := bwlimit_conflict(self.bwlimit, self.rclone_args):
            raise ValueError(error)
        return self

    @field_validator("local_dir")
    @classmethod
    def _local_dir(cls, v: str | None) -> str | None:
        return v if v is None else check_local_dir(v)

    @field_validator("remote_dir")
    @classmethod
    def _remote_dir(cls, v: str | None) -> str | None:
        return v if v is None else check_remote_dir(v)

    @field_validator("rclone_args")
    @classmethod
    def _rclone_args(cls, v: list[str], info: ValidationInfo) -> list[str]:
        check_rclone_args(v)
        if info.data.get("sync_mode") == SyncMode.TWO_WAY and (error := two_way_rclone_args_error(v)):
            raise ValueError(error)
        return v

    @field_validator("rclone_filter")
    @classmethod
    def _rclone_filter(cls, v: list[str] | None) -> list[str] | None:
        return v if v is None else check_rclone_filter(v)


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    local_dir: str | None = Field(default=None, min_length=1, max_length=1024)
    remote_dir: str | None = Field(default=None, min_length=1, max_length=1024)
    debounce_seconds: int | None = Field(
        default=None, ge=DEBOUNCE_SECONDS_MIN, le=DEBOUNCE_SECONDS_MAX,
    )
    pull_interval_minutes: int | None = Field(
        default=None, ge=PULL_INTERVAL_MINUTES_MIN, le=PULL_INTERVAL_MINUTES_MAX,
    )
    rclone_filter: RcloneFilter | None = None
    rclone_args: RcloneArgs | None = None
    max_retries: int | None = Field(default=None, ge=MAX_RETRIES_MIN, le=MAX_RETRIES_MAX)
    # Switching to two_way makes the next sync a resync (the union of both sides).
    sync_mode: SyncMode | None = None
    # Hides (true) or shows again (false) the mirror-mode notice of this profile.
    mirror_notice_dismissed: bool | None = None
    # Omitted: unchanged. null (or "" for bwlimit) clears the limit / the window.
    bwlimit: str | None = Field(default=None, max_length=MAX_BWLIMIT_LENGTH)
    sync_window: SyncWindow | None = None

    @field_validator("bwlimit")
    @classmethod
    def _bwlimit(cls, v: str | None) -> str | None:
        return check_bwlimit(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return v if v is None else check_profile_name(v)

    @field_validator("local_dir")
    @classmethod
    def _local_dir(cls, v: str | None) -> str | None:
        return v if v is None else check_local_dir(v)

    @field_validator("remote_dir")
    @classmethod
    def _remote_dir(cls, v: str | None) -> str | None:
        return v if v is None else check_remote_dir(v)

    @field_validator("rclone_args")
    @classmethod
    def _rclone_args(cls, v: list[str] | None) -> list[str] | None:
        return v if v is None else check_rclone_args(v)

    @field_validator("rclone_filter")
    @classmethod
    def _rclone_filter(cls, v: list[str] | None) -> list[str] | None:
        return v if v is None else check_rclone_filter(v)


class ProfileResponse(BaseModel):
    id: int
    slug: str
    name: str
    local_dir: str
    remote_dir: str
    debounce_seconds: int = 5
    pull_interval_minutes: int = 5
    rclone_filter: list[str] = []
    rclone_args: list[str] = []
    max_retries: int = 3
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    sync_mode: SyncMode = SyncMode.MIRROR
    # The user dismissed the mirror-mode notice for this profile.
    mirror_notice_dismissed: bool = False
    bwlimit: str | None = None
    sync_window: SyncWindow | None = None


class ProfileStatusResponse(ProfileResponse):
    state: SyncState = SyncState.IDLE
    last_sync: datetime | None = None
    current_job_id: int | None = None
    files_processed: int = 0
    errors: int = 0
    pending_changes: int = 0
    intervals_paused: bool = False
    paused_at: datetime | None = None
    last_error: str | None = None  # why the last sync failed or was refused
    # Files one sync may delete before rclone stops it (the profile's own
    # --max-delete, else OMNISYNC_MAX_DELETE); None means no limit. A
    # two-way sync applies it to each side and checks before changing anything.
    max_delete: int | None = None
    resync_required: bool = False  # see SyncStatusResponse
    user_paused: bool = False  # see SyncStatusResponse
    progress: SyncProgress | None = None
    outside_sync_window: bool = False
    next_window_start: datetime | None = None
    waiting_for_window: bool = False


class PauseAllResponse(BaseModel):
    """POST /profiles/pause-all and /profiles/resume-all: what happened per profile (by slug)."""
    changed: list[str] = []
    # Already paused by the user (pause-all) / not paused by the user (resume-all).
    unchanged: list[str] = []
    # resume-all: still paused for another reason (pending differences, a
    # restore, a needed resync), slug -> why. That pause needs its own resume.
    still_paused: dict[str, str] = {}


class TrashSide(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class TrashEntry(BaseModel):
    """A file in a profile's .omnisync-trash.

    ``id`` is the path inside the trash (``<folder>/<path>``); ``folder``
    is the sync's timestamp folder and ``path`` where the file was, relative
    to the synced folder. ``trashed_at`` comes from the folder name (None
    for folders not named by OmniSync).
    """
    id: str
    folder: str
    path: str
    size: int | None = None
    modified: datetime | None = None
    trashed_at: datetime | None = None


class TrashListResponse(BaseModel):
    side: TrashSide
    entries: list[TrashEntry] = []
    total_files: int = 0
    total_bytes: int = 0
    # More files than listed (the list is capped; the totals count them all).
    truncated: bool = False


MAX_TRASH_ITEMS = 1000


class TrashActionRequest(BaseModel):
    side: TrashSide
    ids: list[Annotated[str, Field(min_length=1, max_length=MAX_FILE_PATH_LENGTH)]] = Field(
        min_length=1, max_length=MAX_TRASH_ITEMS,
    )
    # Restore only: also replace a target that is newer than the trashed
    # file (the replaced version is moved to the trash first).
    overwrite: bool = False


class TrashItemError(BaseModel):
    id: str
    # not_found / target_newer / target_is_folder / invalid / failed
    code: str
    message: str


class TrashActionResponse(BaseModel):
    done: list[str] = []
    failed: list[TrashItemError] = []


# --- Global config schemas ---


class GlobalConfigResponse(BaseModel):
    log_level: str = "INFO"
    # Days of sync and backup job history kept; 0 keeps it all. The newest
    # jobs of each profile and backup target are kept regardless.
    history_days: int = 90


class GlobalConfigUpdateRequest(BaseModel):
    log_level: LogLevel | None = None
    history_days: int | None = Field(default=None, ge=0, le=3650)


# --- Backup target & job schemas ---


class BackupTargetType(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CUSTOM_REMOTE = "custom_remote"


class BackupMode(str, Enum):
    ARCHIVE = "archive"
    MIRROR = "mirror"


class BackupJobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RestoreScope(str, Enum):
    LOCAL_ONLY = "local_only"
    REMOTE_ONLY = "remote_only"
    BOTH = "both"


def check_backup_target_path(value: str) -> str:
    """Checks on a backup target_path that do not depend on the target type."""
    _no_control_chars(value, "target_path")
    if value.startswith("-"):
        raise ValueError("target_path may not start with '-'")
    return value


def check_backup_remote_name(value: str | None) -> str | None:
    """An rclone remote name, or None ('' counts as none: the forms send it)."""
    if not value:
        return None
    if not REMOTE_PATH_PATTERN.fullmatch(f"{value}:"):
        raise ValueError("remote_name must be an rclone remote name (letters, digits, '_' and '-')")
    return value


def check_backup_target(target_type: BackupTargetType, target_path: str, remote_name: str | None) -> None:
    """Raise ValueError when target_path does not fit target_type.

    target_path goes to rclone (and, for local targets, to the filesystem)
    as stored: a local target is an absolute path, a remote target
    '<remote>:<path>'. A custom remote target checks liveness on
    remote_name but writes to target_path, so both must name one remote.
    """
    check_backup_target_path(target_path)
    check_backup_remote_name(remote_name)
    if target_type == BackupTargetType.LOCAL:
        if not target_path.startswith("/"):
            raise ValueError("target_path of a local target must be an absolute path, e.g. /mnt/backup")
        check_not_data_dir(target_path, "target_path")
        return
    if not REMOTE_PATH_PATTERN.match(target_path):
        raise ValueError("target_path of a remote target must be '<remote>:<path>', e.g. gdrive:Backups")
    remote = target_path.split(":", 1)[0]
    if target_type == BackupTargetType.CUSTOM_REMOTE and remote_name and remote != remote_name:
        raise ValueError(f"target_path must be on the remote '{remote_name}' named in remote_name")


# An encryption passphrase: long enough not to be guessed in an afternoon.
PASSPHRASE_MIN_LENGTH = 8


def check_encryption_passphrase(value: str | None) -> str | None:
    """A passphrase for an encrypted target, or None ('' counts as none)."""
    if not value:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("encryption_passphrase may not contain control characters or line breaks")
    if len(value) < PASSPHRASE_MIN_LENGTH:
        raise ValueError(f"encryption_passphrase must be at least {PASSPHRASE_MIN_LENGTH} characters long")
    return value


class BackupTargetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    target_path: str = Field(..., min_length=1, max_length=1024)
    target_type: BackupTargetType
    remote_name: str | None = None
    retention_days: int = Field(default=7, ge=1, le=365)
    # Retention never deletes the newest keep_last snapshots, however old.
    keep_last: int = Field(default=3, ge=1, le=1000)
    frequency_hours: int = Field(default=24, ge=1, le=8760)
    backup_mode: BackupMode = BackupMode.MIRROR
    enabled: bool = True
    # Encrypt everything written to the target (rclone crypt) with this
    # passphrase. Stored obscured, never returned; losing it loses the backups.
    encryption_passphrase: str | None = Field(default=None, max_length=1024)
    # Compare the backup with the folder after each run.
    verify_after_backup: bool = True

    @model_validator(mode="after")
    def _check_target(self) -> "BackupTargetCreateRequest":
        self.encryption_passphrase = check_encryption_passphrase(self.encryption_passphrase)
        self.remote_name = check_backup_remote_name(self.remote_name)
        check_backup_target(self.target_type, self.target_path, self.remote_name)
        return self


class BackupTargetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    target_path: str | None = Field(default=None, min_length=1, max_length=1024)
    target_type: BackupTargetType | None = None
    remote_name: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    keep_last: int | None = Field(default=None, ge=1, le=1000)
    frequency_hours: int | None = Field(default=None, ge=1, le=8760)
    backup_mode: BackupMode | None = None
    enabled: bool | None = None
    # Present: set, change ('' or null: remove) the target's passphrase. Only
    # allowed while the target's location holds no backups: the existing
    # ones could no longer be read.
    encryption_passphrase: str | None = Field(default=None, max_length=1024)
    verify_after_backup: bool | None = None

    @field_validator("encryption_passphrase")
    @classmethod
    def _check_passphrase(cls, v: str | None) -> str | None:
        return check_encryption_passphrase(v)

    # The type-dependent check (check_backup_target) needs the stored values
    # for the fields a partial update leaves out; the route runs it.
    @field_validator("target_path")
    @classmethod
    def _check_target_path(cls, v: str | None) -> str | None:
        return None if v is None else check_backup_target_path(v)

    @field_validator("remote_name")
    @classmethod
    def _check_remote_name(cls, v: str | None) -> str | None:
        return check_backup_remote_name(v)


class BackupTargetResponse(BaseModel):
    id: int
    profile_id: int
    name: str
    target_path: str
    target_type: BackupTargetType
    remote_name: str | None
    retention_days: int
    keep_last: int
    frequency_hours: int
    backup_mode: BackupMode
    enabled: bool
    # Encrypted with a passphrase (the passphrase itself is never returned).
    encrypted: bool = False
    verify_after_backup: bool = False
    # Enabled, and no backup completed for more than twice its frequency.
    overdue: bool = False
    last_liveness_ok: bool | None = None
    last_liveness_error: str | None = None
    last_backup_at: datetime | None = None
    last_backup_status: str | None = None
    # Verification of the last backup: "verified", "failed" or None.
    last_verify_status: str | None = None
    last_verify_message: str | None = None
    next_scheduled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BackupJobResponse(BaseModel):
    """A backup or restore run. Starts answer 202 with it running; follow it with GET .../jobs/{id}.

    A failed or skipped run has a stable ``error_code`` (docs/api-errors.md)
    and ``error_message``, a fixed message for that code (or OmniSync's own
    explanation of a refusal); rclone's output stays in the OmniSync log.
    """
    id: int
    target_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: BackupJobStatus
    direction: str
    size_bytes: int | None = None
    snapshot_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    # Verification after the backup: "verified", "failed" or None (not run).
    verify_status: str | None = None
    verify_message: str | None = None


class SnapshotResponse(BaseModel):
    snapshot_id: str
    created_at: datetime
    size_bytes: int | None = None
    status: str
    # The most recent backup of the target.
    latest: bool = False


def check_snapshot_id(value: str) -> str:
    """A snapshot is one entry of the target's versions folder, never a path."""
    _no_control_chars(value, "snapshot_id")
    if "/" in value or "\\" in value:
        raise ValueError("snapshot_id may not contain path separators")
    if ".." in value or value.strip() in ("", "."):
        raise ValueError("snapshot_id is not a valid snapshot name")
    return value


class RestoreRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=255)
    restore_scope: RestoreScope

    @field_validator("snapshot_id")
    @classmethod
    def _snapshot_id(cls, v: str) -> str:
        return check_snapshot_id(v)


class SnapshotFileEntry(BaseModel):
    """One file or folder of a snapshot, as the snapshot browser shows it."""
    path: str                       # relative to the backed-up folder; folders without a trailing '/'
    name: str
    is_dir: bool
    size: int | None = None         # a folder: the total of its files
    mod_time: datetime | None = None
    file_count: int | None = None   # a folder: how many files it holds, at any depth


class SnapshotFilesResponse(BaseModel):
    snapshot_id: str
    path: str                       # the folder listed ('' for the top); ignored by a search
    search: str | None = None
    entries: list[SnapshotFileEntry]
    total: int                      # entries matching, before paging
    offset: int
    limit: int
    snapshot_files: int             # files in the whole snapshot


def check_snapshot_rel_path(value: str) -> str:
    """A file or folder path inside a snapshot: relative, without '..' or empty parts."""
    _no_control_chars(value, "path")
    value = value.strip("/")
    if not value:
        raise ValueError("path may not be empty")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"path {value!r} must be a plain relative path inside the snapshot")
    return value


class RestoreFilesRequest(BaseModel):
    """Restore chosen files and folders of a snapshot."""
    snapshot_id: str = Field(min_length=1, max_length=255)
    # Files and folders (a folder means everything in it), relative paths.
    paths: list[str] = Field(min_length=1, max_length=10000)
    # Where to: None restores them to their original place in the profile's
    # local folder; an absolute local folder restores them there instead.
    target_dir: str | None = Field(default=None, max_length=1024)

    @field_validator("snapshot_id")
    @classmethod
    def _snapshot_id(cls, v: str) -> str:
        return check_snapshot_id(v)

    @field_validator("paths")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return sorted({check_snapshot_rel_path(p) for p in v})

    @field_validator("target_dir")
    @classmethod
    def _target_dir(cls, v: str | None) -> str | None:
        if not v:
            return None
        _no_control_chars(v, "target_dir")
        if not v.startswith("/"):
            raise ValueError("target_dir must be an absolute local path, e.g. /home/you/Restored")
        return check_not_data_dir(v, "target_dir")


class RestorePreviewSide(BaseModel):
    """What a full restore would change in one destination folder."""
    side: Literal["local", "remote"]
    path: str
    added: int        # in the snapshot, missing in the folder
    replaced: int     # in both, but with another size or modification time
    removed: int      # in the folder, not in the snapshot (moved to the safety folder)
    unchanged: int
    # A few example paths per kind, for the dialog.
    added_examples: list[str] = []
    replaced_examples: list[str] = []
    removed_examples: list[str] = []


class RestorePreviewResponse(BaseModel):
    snapshot_id: str
    restore_scope: RestoreScope
    sides: list[RestorePreviewSide]


# --- Remote management schemas ---


# --- Aggregate status schemas ---


class PausedProfileSummary(BaseModel):
    """A paused profile in the aggregate status response."""
    slug: str
    name: str
    pending_changes: int = 0
    paused_at: datetime | None = None
    user_paused: bool = False  # paused by the user (Pause / Pause all), see SyncStatusResponse


class ProfileSummary(BaseModel):
    """Summary of a single profile in the aggregate status."""
    slug: str
    name: str
    state: SyncState
    last_sync: datetime | None = None
    pending_changes: int = 0
    intervals_paused: bool = False
    last_error: str | None = None  # why the last sync failed or was refused
    resync_required: bool = False  # see SyncStatusResponse
    user_paused: bool = False  # see SyncStatusResponse
    progress: SyncProgress | None = None


class AggregateStatusResponse(BaseModel):
    """Aggregated status across all running sync engines."""
    overall_state: SyncState = SyncState.IDLE
    total_pending_changes: int = 0
    paused_profiles: list[PausedProfileSummary] = []
    profiles_summary: list[ProfileSummary] = []


class RemoteStorageInfoResponse(BaseModel):
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    trashed_bytes: int | None = None
    supported: bool = True


class RemoteTestResponse(BaseModel):
    success: bool
    latency_ms: int | None = None
    error: str | None = None
    # The provider refused the credentials: reconnect (OAuth) or edit them.
    auth_error: bool = False


class RemoteDependencyProfile(BaseModel):
    slug: str
    name: str


class RemoteDependencyBackupTarget(BaseModel):
    profile_slug: str
    target_name: str
    target_id: int


class RemoteDependenciesResponse(BaseModel):
    profiles: list[RemoteDependencyProfile] = []
    backup_targets: list[RemoteDependencyBackupTarget] = []

