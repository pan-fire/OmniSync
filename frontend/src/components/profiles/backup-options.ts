import type { BackupMode, BackupTargetType } from '@/types';

/** Choices shared by the backup target form and the profile form. Labels: backups.types.* / backups.modes.*. */
export const BACKUP_TARGET_TYPES: readonly BackupTargetType[] = ['local', 'remote', 'custom_remote'];
export const BACKUP_MODES: readonly BackupMode[] = ['archive', 'mirror'];

/**
 * "<remote>:" at the start of an rclone path, the rule the backend applies to
 * a profile's remote_dir (REMOTE_PATH_PATTERN in backend/api/schemas.py) and
 * uses to tell a remote path from a local one (backend/services/path_overlap.py).
 */
const REMOTE_PREFIX = /^([A-Za-z0-9_][A-Za-z0-9_-]*):/;

/** Targets whose target_path goes to rclone as `remote:path`. */
export function isRemoteTargetType (type: BackupTargetType): boolean {
  return type === 'remote' || type === 'custom_remote';
}

/** The remote name of `remote:path`, or null for anything else. */
export function remoteOfPath (path: string): string | null {
  return REMOTE_PREFIX.exec(path.trim())?.[1] ?? null;
}

/** `path` on `remote`: replaces an existing `remote:` prefix, else adds one. */
export function withRemote (path: string, remote: string): string {
  const trimmed = path.trim();
  const rest = REMOTE_PREFIX.test(trimmed)
    ? trimmed.replace(REMOTE_PREFIX, '')
    : trimmed.replace(/^\/+/, '');
  return `${remote}:${rest}`;
}

/**
 * Why a backup target path cannot be used, as an i18n key, or null.
 *
 * Mirrors check_backup_target in backend/api/schemas.py, which refuses the
 * same paths with a 422: target_path goes to rclone as stored, so a local
 * target needs an absolute path and a remote one "remote:path" (without it
 * the path would be read as a local folder inside the container). A custom
 * remote target checks liveness on remote_name but writes to target_path,
 * so both must name the same remote.
 */
export function backupTargetPathError (
  type: BackupTargetType,
  path: string,
  remoteName?: string | null
): string | null {
  const trimmed = path.trim();
  if (!trimmed) return 'backups.validation.pathRequired';
  if (!isRemoteTargetType(type)) return trimmed.startsWith('/') ? null : 'backups.validation.localPathAbsolute';
  const remote = remoteOfPath(trimmed);
  if (!remote) return 'backups.validation.remotePathFormat';
  if (type === 'custom_remote' && remoteName && remote !== remoteName) {
    return 'backups.validation.remotePathMismatch';
  }
  return null;
}

/**
 * Where the folder browser opens for a backup target path, or null when it
 * cannot: a remote target needs its remote first (`remote` is the chosen
 * custom remote or the profile's remote). A path on another remote opens
 * that remote's root instead, so a pick always lands on `remote`.
 */
export function backupBrowseStart (
  type: BackupTargetType,
  path: string,
  remote: string | null | undefined
): { mode: 'local' | 'remote'; initialPath: string } | null {
  const trimmed = path.trim();
  if (!isRemoteTargetType(type)) return { mode: 'local', initialPath: trimmed || '/' };
  if (!remote) return null;
  return {
    mode:        'remote',
    initialPath: remoteOfPath(trimmed) === remote ? trimmed : `${remote}:`,
  };
}
