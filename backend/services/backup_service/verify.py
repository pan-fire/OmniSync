"""Verifying a backup right after it was written."""

from __future__ import annotations

import asyncio
import os

from backend.db.models import BackupTarget
from backend.services.rclone import redact_secrets
from backend.services.sync_engine import remote_join
from backend.services.backup_service.common import BACKUP_FILTER
from backend.services.backup_service.base import BackupBase
from backend.services.backup_service.archive import _ARCHIVE_ERRORS, _read_back_archive

# Verification outcomes (BackupJob.verify_status)
VERIFIED = "verified"
VERIFY_FAILED = "failed"
# Bytes of an uploaded encrypted archive read back to test its decryption:
# the crypt header plus the first block.
DECRYPT_TEST_BYTES = 64 * 1024


class VerifyMixin(BackupBase):
    """Checks a mirror's current/ against the folder, or an archive read back and stored in full."""

    async def _verify_mirror(
        self, target: BackupTarget, source: str, current: str, file_count: int,
    ) -> tuple[str, str]:
        """Compare current/ with the folder just backed up: every file there, same size and hash.

        One way (``--one-way``): only what the folder holds is checked. rclone
        lists current/ once and hashes the local files; it downloads nothing
        (for an encrypted target, cryptcheck reads each file's 32-byte header
        to hash the local file the same way). A file changed while the
        backup ran is reported too; the next backup includes it.
        """
        if file_count == 0:
            return VERIFIED, "Nothing to verify: the folder was empty."
        try:
            parsed, error = await self._rclone.verify_copy(
                source, current, BACKUP_FILTER, encrypted=bool(target.encryption_password),
            )
        except Exception as exc:
            return VERIFY_FAILED, f"The verification could not run: {redact_secrets(str(exc))}"[:4096]
        if error is not None:
            return VERIFY_FAILED, redact_secrets(error)[:4096]
        if parsed.has_differences:
            parts = []
            if parsed.differ:
                parts.append(f"{len(parsed.differ)} file(s) differ from the folder")
            if parsed.local_only:
                parts.append(f"{len(parsed.local_only)} file(s) are missing from the backup")
            example = (parsed.differ + parsed.local_only)[0]
            return VERIFY_FAILED, (
                f"{' and '.join(parts)}, e.g. '{example}'. A file changed while the backup ran "
                "shows up here too; run the backup again to see whether the difference stays."
            )[:4096]
        how = "size and checksum (cryptcheck)" if target.encryption_password else "size and checksum"
        return VERIFIED, f"{parsed.matched} file(s) match the folder ({how})."

    async def _verify_archive(
        self, target: BackupTarget, temp_path: str, filename: str, size: int,
    ) -> tuple[str, str]:
        """Check an archive just written: readable to the end, and stored at the target in full.

        A local target's copy is read back from the target itself. An archive
        on a remote is not downloaded again: the local copy is read back, the
        stored size compared and, when encrypted, its first block downloaded
        and decrypted (which also proves the passphrase).
        """
        root = self.storage_root(target)
        try:
            source = os.path.join(target.target_path, filename) if self._plain_local(target) else temp_path
            try:
                files, _ = await asyncio.to_thread(_read_back_archive, source)
            except _ARCHIVE_ERRORS as exc:
                return VERIFY_FAILED, f"The archive cannot be read back: {exc}"
            stored = (await self._rclone.lsjson_paths(root, [filename])).get(filename)
            if stored is None:
                return VERIFY_FAILED, f"{filename} is not at the target after the upload."
            stored_size = stored.get("Size")
            if isinstance(stored_size, int) and stored_size >= 0 and stored_size != size:
                return VERIFY_FAILED, (f"{filename} at the target has {stored_size:,} bytes instead of "
                                       f"{size:,}: the upload is incomplete.")
            how = "read back in full" if self._plain_local(target) else "size matches"
            if target.encryption_password:
                head = await self._rclone.cat_head(remote_join(root, filename), DECRYPT_TEST_BYTES)

                def local_head() -> bytes:
                    with open(temp_path, "rb") as fh:
                        return fh.read(DECRYPT_TEST_BYTES)

                if head != await asyncio.to_thread(local_head):
                    return VERIFY_FAILED, f"{filename} at the target does not decrypt to the archive written."
                how += ", decryption tested"
            return VERIFIED, f"Archive of {files} file(s) verified ({how})."
        except Exception as exc:
            return VERIFY_FAILED, f"The verification could not run: {redact_secrets(str(exc))}"[:4096]
