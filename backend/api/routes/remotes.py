"""Remote management endpoints for OmniSync."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.audit import audited
from backend.api.errors import SEE_LOG, api_error
from backend.api.schemas import (
    BackupTargetType,
    ImportCandidate,
    ImportConfigRequest,
    ImportPreviewResponse,
    ImportRemotesRequest,
    ImportRemotesResponse,
    RemoteConfigField,
    RemoteConfigResponse,
    RemoteDependenciesResponse,
    RemoteDependencyBackupTarget,
    RemoteDependencyProfile,
    RemoteResponse,
    RemoteStorageInfoResponse,
    RemoteTestResponse,
    UpdateRemoteRequest,
)
from backend.db.models import BackupTarget, Remote, SyncProfile
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services import remote_auth
from backend.services.provider_registry import (
    ProviderInfo,
    get_provider,
    remote_capabilities,
    validate_remote_name,
    validate_remote_params,
)
from backend.services.rclone import RESERVED_NAME_MESSAGE, RcloneService, is_reserved_remote_name
from backend.services.rclone_import import (
    ImportParseError,
    ImportSection,
    SectionLookup,
    local_reference_problems,
    parse_rclone_config,
    rewrite_references,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# How rclone says a backend has no About call. 1.75 prints
# "<remote> doesn't support about"; older releases and some backends say
# "not supported" / "unsupported".
_ABOUT_UNSUPPORTED = ("doesn't support about", "does not support about", "not supported", "unsupported")

# Module-level references set by main.py at startup
_rclone_service: RcloneService | None = None
_db_factory: async_sessionmaker[AsyncSession] | None = None


def set_rclone_service(service: RcloneService | None) -> None:
    """Set the rclone service reference."""
    global _rclone_service
    _rclone_service = service


def set_db_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """Set the database session factory."""
    global _db_factory
    _db_factory = factory


def _get_rclone() -> RcloneService:
    if _rclone_service is None:
        raise api_error(503, "service_unavailable", "Rclone service not available")
    return _rclone_service


def _check_name(name: str) -> None:
    """422 for a name that is not an rclone remote name.

    The name goes onto rclone's command line; one such as
    '--log-file=/tmp/x' would otherwise be read as an option.
    """
    if not validate_remote_name(name):
        raise api_error(
            422, "invalid_remote_name",
            "Invalid remote name: use letters, digits, '_' and '-', not starting with '-'",
        )


def _get_db_factory() -> async_sessionmaker[AsyncSession]:
    if _db_factory is None:
        raise api_error(503, "service_unavailable", "Database not available")
    return _db_factory


@router.get("/remotes", response_model=list[RemoteResponse])
async def list_remotes() -> list[RemoteResponse]:
    """Return the list of configured rclone remotes."""
    rclone = _get_rclone()
    try:
        remotes = await rclone.list_remotes()
    except Exception:
        logger.exception("Listing remotes failed")
        raise api_error(500, "internal_error", f"Failed to list remotes. {SEE_LOG}")
    if _db_factory is not None and remotes:
        async with _db_factory() as session:
            verified = {
                r.name: r.last_verified
                for r in (await session.execute(select(Remote))).scalars().all()
            }
        for remote in remotes:
            when = verified.get(remote.name)
            remote.last_verified = when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when
    for remote in remotes:
        caps = remote_capabilities(remote.type)
        remote.provider_id = caps.provider_id
        remote.editable = caps.editable
        remote.reconnectable = caps.reconnectable
        remote.auth_error = remote_auth.auth_failed(remote.name)
    return remotes


async def _existing_names(rclone: RcloneService) -> set[str]:
    try:
        return {r.name for r in await rclone.list_remotes()}
    except Exception:
        logger.exception("Listing remotes failed")
        raise api_error(500, "internal_error", f"Failed to list remotes. {SEE_LOG}")


# --- Import an rclone.conf ---
# Declared before the /remotes/{name}/... routes. The file's values are
# never logged or returned, only section and option names.


def _parse_import(content: str) -> list[ImportSection]:
    try:
        return parse_rclone_config(content)
    except ImportParseError as e:
        raise api_error(422, "invalid_rclone_config", str(e))


def _section_lookup(rclone: RcloneService, first: Mapping[str, Mapping[str, str]]) -> SectionLookup:
    """A remote's settings by name: from ``first`` (the remotes of the
    upload), else from the current rclone.conf. Values stay in memory."""

    async def lookup(name: str) -> Mapping[str, str] | None:
        if name in first:
            return first[name]
        try:
            return await rclone.remote_section(name)
        except ValueError:
            return None
        except Exception:
            logger.exception("Reading remote '%s' failed", name)
            raise api_error(500, "internal_error", f"Could not read the existing remotes. {SEE_LOG}")

    return lookup


@router.post("/remotes/import/preview", response_model=ImportPreviewResponse)
async def preview_import(request: ImportConfigRequest) -> ImportPreviewResponse:
    """List the remotes of an uploaded rclone.conf: which can be imported,
    which clash with an existing remote of the same name, which are refused."""
    rclone = _get_rclone()
    try:
        sections = parse_rclone_config(request.content)
    except ImportParseError as e:
        return ImportPreviewResponse(errors=[str(e)])
    existing = await _existing_names(rclone)
    lookup = _section_lookup(rclone, {sec.name: sec.options for sec in sections})
    for sec in sections:
        sec.problems += await local_reference_problems(sec.options, lookup)
    return ImportPreviewResponse(remotes=[
        ImportCandidate(
            name=sec.name, type=sec.type, exists=sec.name in existing, problems=sec.problems,
            keys=[k for k in sec.options if k != "type"],
        )
        for sec in sections
    ])


@router.post("/remotes/import", response_model=ImportRemotesResponse)
@audited("remote.import", lambda kw: {"remotes": [s.name or s.source for s in kw["request"].remotes]})
async def import_remotes(request: ImportRemotesRequest) -> ImportRemotesResponse:
    """Add the selected remotes of an uploaded rclone.conf, all or none.

    Each selection names a section of the file and the name to add it under
    (to resolve a clash with an existing remote). Values are copied verbatim;
    references between imported remotes (a crypt remote's ``remote``)
    follow a rename. A clash is 409, a refused section or name 422.
    """
    rclone = _get_rclone()
    sections = {sec.name: sec for sec in _parse_import(request.content)}

    renames: dict[str, str] = {}
    errors: list[str] = []
    for sel in request.remotes:
        target = sel.name or sel.source
        sec = sections.get(sel.source)
        if sec is None:
            errors.append(f"'{sel.source}' is not in the file")
        elif not sec.importable:
            errors.append(f"'{sel.source}' cannot be imported: {'; '.join(sec.problems)}")
        elif not validate_remote_name(target):
            errors.append(f"'{target}' is not a valid remote name")
        elif is_reserved_remote_name(target):
            errors.append(f"'{target}' cannot be used: {RESERVED_NAME_MESSAGE}")
        elif sel.source in renames:
            errors.append(f"'{sel.source}' is selected twice")
        elif target in renames.values():
            errors.append(f"Two remotes would be named '{target}'")
        else:
            renames[sel.source] = target
    if errors:
        raise api_error(422, "invalid_import", "; ".join(errors), errors=errors)

    existing = await _existing_names(rclone)
    clashes = sorted(t for t in renames.values() if t in existing)
    if clashes:
        raise api_error(409, "name_clash", "Remotes with these names exist already: " + ", ".join(clashes), names=clashes)

    moved = {src: dst for src, dst in renames.items() if src != dst}
    new = {dst: rewrite_references(sections[src].options, moved) for src, dst in renames.items()}
    lookup = _section_lookup(rclone, new)
    for src, dst in renames.items():
        problems = await local_reference_problems(new[dst], lookup)
        if problems:
            errors.append(f"'{src}' cannot be imported: {'; '.join(problems)}")
    if errors:
        raise api_error(422, "invalid_import", "; ".join(errors), errors=errors)
    try:
        await rclone.add_remotes(new)
    except ValueError as e:
        if "already exist" in str(e):
            raise api_error(409, "name_clash", "Remotes with these names exist already: " + ", ".join(sorted(new)), names=sorted(new))
        logger.warning("Import refused: %s", e)
        raise api_error(422, "invalid_import", "The remotes could not be imported.")
    except Exception:
        logger.exception("Importing %d remote(s) failed", len(new))
        raise api_error(500, "internal_error", f"Failed to import the remotes. {SEE_LOG}")
    return ImportRemotesResponse(imported=list(new))


# --- Edit a remote ---


async def _editable_section(rclone: RcloneService, name: str) -> tuple[dict[str, str], ProviderInfo]:
    """The remote's settings and its wizard provider; 404 or 409 if it has none."""
    _check_name(name)
    try:
        section = await rclone.remote_section(name)
    except Exception:
        logger.exception("Reading remote '%s' failed", name)
        raise api_error(500, "internal_error", f"Could not read the remote. {SEE_LOG}")
    if section is None:
        raise api_error(404, "remote_not_found", f"Remote '{name}' not found")
    provider = get_provider(section.get("type", ""))
    if provider is None:
        raise api_error(
            409, "remote_not_editable",
            f"Remotes of type '{section.get('type', '')}' cannot be edited here; use rclone config.",
        )
    return section, provider


@router.get("/remotes/{name}/config", response_model=RemoteConfigResponse)
async def get_remote_config(name: str) -> RemoteConfigResponse:
    """The remote's settings for the edit form. Secrets (passwords, keys,
    client secrets) only say whether they are set; tokens are not included."""
    section, provider = await _editable_section(_get_rclone(), name)
    known = {f.name for f in provider.fields}
    return RemoteConfigResponse(
        name=name,
        type=section.get("type", ""),
        provider_id=provider.id,
        fields=[
            RemoteConfigField(
                name=f.name,
                value="" if f.secret else section.get(f.name, ""),
                is_set=bool(section.get(f.name)),
                secret=f.secret,
            )
            for f in provider.fields
        ],
        other_keys=[k for k in section if k != "type" and k not in known],
    )


@router.put("/remotes/{name}")
@audited("remote.edit", lambda kw: {"fields": sorted(kw["request"].params)}, remote="name")
async def update_remote(name: str, request: UpdateRemoteRequest) -> dict[str, str]:
    """Change a remote's settings without deleting it.

    Validated like a new remote (only the provider's fields, see
    validate_remote_params). Fields left out stay as they are; a non-secret
    field set to "" is removed; a secret set to "" is kept, another value
    replaces it (obscured where rclone expects that); ``clear`` removes
    secrets. OAuth remotes change through a reconnect instead.
    """
    from backend.api.routes.wizard import existing_remote_names, obscure_secrets

    rclone = _get_rclone()
    section, provider = await _editable_section(rclone, name)
    if not remote_capabilities(provider.id).editable:
        raise api_error(
            409, "reconnect_required",
            f"{provider.display_name} remotes sign in with OAuth: reconnect them instead.",
        )

    fields = {f.name: f for f in provider.fields}
    existing = await existing_remote_names(rclone, provider)
    errors = validate_remote_params(provider, request.params, has_token=False, existing_remotes=existing, own_name=name)
    for key in request.clear:
        f = fields.get(key)
        if f is None or not f.secret:
            errors.append(f"'{key}' is not a {provider.display_name} secret")
        elif request.params.get(key):
            errors.append(f"'{key}' cannot be both replaced and cleared")
    if errors:
        raise api_error(422, "invalid_params", "; ".join(errors), errors=errors)

    set_values: dict[str, str] = {}
    remove: set[str] = set(request.clear)
    for key, value in request.params.items():
        if value != "":
            set_values[key] = value
        elif not fields[key].secret:
            remove.add(key)
    missing = [f"'{f.name}' is required" for f in provider.fields if f.required
               and not (set_values.get(f.name) or (section.get(f.name) and f.name not in remove))]
    if missing:
        raise api_error(422, "invalid_params", "; ".join(missing), errors=missing)

    set_values = await obscure_secrets(rclone, provider, set_values, name)
    try:
        await rclone.update_remote(name, provider.id, set_values, remove)
    except ValueError as e:
        logger.warning("Update of remote '%s' refused: %s", name, e)
        raise api_error(409, "remote_changed", f"Remote '{name}' changed meanwhile; reload and try again.")
    except Exception:
        logger.exception("Updating remote '%s' failed", name)
        raise api_error(500, "internal_error", f"Failed to update the remote. {SEE_LOG}")
    # New credentials: the old sign-in failure no longer applies.
    remote_auth.clear_auth_failed(name)
    return {"detail": f"Remote '{name}' updated"}


@router.get("/remotes/{name}/about", response_model=RemoteStorageInfoResponse)
async def get_remote_about(name: str) -> RemoteStorageInfoResponse:
    """Get storage info for a remote."""
    _check_name(name)
    rclone = _get_rclone()
    try:
        info = await rclone.about(name)
        return RemoteStorageInfoResponse(
            total_bytes=info.get("total"),
            used_bytes=info.get("used"),
            free_bytes=info.get("free"),
            trashed_bytes=info.get("trashed"),
            supported=True,
        )
    except RcloneError as exc:
        err = str(exc).lower()
        if any(phrase in err for phrase in _ABOUT_UNSUPPORTED):
            return RemoteStorageInfoResponse(supported=False)
        logger.warning("rclone about failed for '%s': %s", name, exc)
        raise api_error(503, "remote_unreachable", f"Remote unreachable. {SEE_LOG}")


@router.post("/remotes/{name}/test", response_model=RemoteTestResponse)
async def test_remote(name: str) -> RemoteTestResponse:
    """Test connectivity to a remote by running 'rclone lsd'."""
    _check_name(name)
    rclone = _get_rclone()
    start = time.monotonic()
    try:
        await rclone._run(["lsd"], use_config_args=False, timeout=15, positional=[f"{name}:"])
        latency_ms = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.warning("Connection test for remote '%s' failed: %s", name, exc)
        auth_error = isinstance(exc, RcloneAuthError)
        if auth_error:
            remote_auth.mark_auth_failed(name)
        return RemoteTestResponse(
            success=False, latency_ms=latency_ms, error=f"Connection test failed. {SEE_LOG}",
            auth_error=auth_error,
        )
    remote_auth.clear_auth_failed(name)
    await _record_verified(rclone, name)
    return RemoteTestResponse(success=True, latency_ms=latency_ms)


async def _record_verified(rclone: RcloneService, name: str) -> None:
    """Store the time of a successful connection test (Remote.last_verified).

    Best effort: the test itself succeeded, and a bookkeeping failure must
    not turn it into an error.
    """
    if _db_factory is None:
        return
    try:
        remote_type = next((r.type for r in await rclone.list_remotes() if r.name == name), "unknown")
        async with _db_factory() as session:
            row = (await session.execute(select(Remote).where(Remote.name == name))).scalar_one_or_none()
            if row is None:
                row = Remote(name=name, type=remote_type)
                session.add(row)
            row.type = remote_type
            row.last_verified = datetime.now(timezone.utc)
            await session.commit()
    except Exception:
        logger.exception("Could not record the successful test of remote '%s'", name)


@router.get("/remotes/{name}/dependencies", response_model=RemoteDependenciesResponse)
async def get_remote_dependencies(name: str) -> RemoteDependenciesResponse:
    """Find sync profiles and backup targets that reference this remote."""
    db = _get_db_factory()
    remote_prefix = f"{name}:"

    async with db() as session:
        # Profiles with remote_dir starting with this remote. autoescape: a
        # '_' in the name is a LIKE wildcard otherwise.
        stmt = select(SyncProfile).where(SyncProfile.remote_dir.startswith(remote_prefix, autoescape=True))
        result = await session.execute(stmt)
        profiles = result.scalars().all()

        dep_profiles = [
            RemoteDependencyProfile(slug=p.slug, name=p.name)
            for p in profiles
        ]

        # Backup targets on this remote: a custom remote target names it in
        # remote_name, a 'remote' target only in its target_path.
        stmt = select(BackupTarget).where(or_(
            BackupTarget.remote_name == name,
            (BackupTarget.target_type != BackupTargetType.LOCAL.value)
            & BackupTarget.target_path.startswith(remote_prefix, autoescape=True),
        )).order_by(BackupTarget.id)
        result = await session.execute(stmt)
        targets = result.scalars().all()

        # Need profile slugs for backup targets
        profile_ids = {t.profile_id for t in targets}
        profile_map: dict[int, str] = {}
        if profile_ids:
            stmt = select(SyncProfile).where(SyncProfile.id.in_(profile_ids))
            result = await session.execute(stmt)
            for p in result.scalars().all():
                profile_map[p.id] = p.slug

        dep_targets = [
            RemoteDependencyBackupTarget(
                profile_slug=profile_map.get(t.profile_id, "unknown"),
                target_name=t.name,
                target_id=t.id,
            )
            for t in targets
        ]

    return RemoteDependenciesResponse(profiles=dep_profiles, backup_targets=dep_targets)


@router.delete("/remotes/{name}")
@audited("remote.delete", remote="name", force="force")
async def delete_remote(name: str, force: bool = Query(False)) -> dict[str, str]:
    """Delete an rclone remote. Returns 409 if dependencies exist unless force=true."""
    rclone = _get_rclone()

    # Check dependencies unless forced
    if not force:
        deps = await get_remote_dependencies(name)
        if deps.profiles or deps.backup_targets:
            raise api_error(409, "remote_in_use", "Remote has dependencies. Use ?force=true to delete anyway.")

    try:
        await rclone.delete_remote(name)
        return {"detail": f"Remote '{name}' deleted"}
    except ValueError:
        raise api_error(404, "remote_not_found", f"Remote '{name}' not found")
    except Exception:
        logger.exception("Deleting remote '%s' failed", name)
        raise api_error(500, "internal_error", f"Failed to delete the remote. {SEE_LOG}")
