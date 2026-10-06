"""The sync test of a new profile: a round trip of a small file, via the provider's API where possible."""

from __future__ import annotations

import configparser
import json

import httpx

from backend.exceptions import RcloneError
from backend.services.rclone.common import _is_remote_name, logger
from backend.services.rclone.process import RcloneBase


def _join(remote_path: str, name: str) -> str:
    """``name`` inside ``remote_path`` as rclone reads it: a leading "/" is kept."""
    if not remote_path or remote_path.endswith("/"):
        return remote_path + name
    return f"{remote_path}/{name}"


class ProbeCleanupError(Exception):
    """The provider did not confirm that the probe file is gone."""


def _check_deleted(resp: httpx.Response, what: str) -> None:
    """A delete answer: 2xx and 404 (already gone) are fine, anything else raises."""
    if resp.is_success or resp.status_code == 404:
        return
    raise ProbeCleanupError(f"{what} answered HTTP {resp.status_code}")


class ProbeMixin(RcloneBase):
    """test_sync() and its Google Drive, Dropbox and OneDrive helpers."""

    async def test_sync(self, local_dir: str, remote_dir: str) -> dict:
        """Run a round-trip sync test with a dummy file.

        Steps:
        1. Verify local_dir exists and is writable
        2. Create a small .omnisync-test file locally
        3. Upload it to the remote via provider API (or rclone copyto)
        4. Verify it exists on the remote
        5. Delete from remote and local
        6. Return step-by-step results

        Args:
            local_dir: Absolute path to the local sync directory.
            remote_dir: Remote path like "gdrive:backup/docs".
        """
        import uuid
        from pathlib import Path

        steps: list[dict[str, object]] = []
        test_filename = f".omnisync-test-{uuid.uuid4().hex[:8]}"
        local_path = Path(local_dir) / test_filename
        # Parse remote_dir into remote_name and remote_path
        if ":" not in remote_dir:
            return {"success": False, "steps": steps, "error": "Invalid remote_dir format — expected 'name:path'"}
        remote_name, remote_path = remote_dir.split(":", 1)
        if not _is_remote_name(remote_name):
            return {"success": False, "steps": steps, "error": "Invalid remote_dir format — expected 'name:path'"}
        # Two spellings of the probe's place. The provider APIs (Drive,
        # Dropbox, OneDrive) take a path from the drive root with no leading
        # or doubled "/"; rclone takes the profile's path exactly as written,
        # where "disk:/abs/dir" (sftp, local, smb) is absolute and
        # "disk:abs/dir" is relative to the remote's home folder.
        remote_file = "/".join([*(part for part in remote_path.split("/") if part), test_filename])
        rclone_target = f"{remote_name}:{_join(remote_path, test_filename)}"

        # Step 1: Check local dir
        try:
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            local_path.write_text(f"OmniSync sync test — {uuid.uuid4()}\n")
            steps.append({"step": "local_write", "ok": True})
        except Exception as e:
            steps.append({"step": "local_write", "ok": False, "error": str(e)})
            return {"success": False, "steps": steps, "error": f"Cannot write to local directory: {e}"}

        # Step 2: Upload to remote
        try:
            uploaded = await self._test_upload(remote_name, remote_file, local_path, rclone_target)
            steps.append({"step": "remote_upload", "ok": uploaded})
            if not uploaded:
                self._cleanup_local(local_path)
                return {"success": False, "steps": steps, "error": "Failed to upload test file to remote"}
        except Exception as e:
            steps.append({"step": "remote_upload", "ok": False, "error": str(e)})
            self._cleanup_local(local_path)
            return {"success": False, "steps": steps, "error": f"Upload failed: {e}"}

        # Step 3: Verify on remote
        try:
            verified = await self._test_verify(remote_name, remote_file, rclone_target)
            steps.append({"step": "remote_verify", "ok": verified})
        except Exception as e:
            steps.append({"step": "remote_verify", "ok": False, "error": str(e)})
            verified = False

        # Step 4: Cleanup
        try:
            await self._test_delete_remote(remote_name, remote_file, rclone_target)
            steps.append({"step": "remote_cleanup", "ok": True})
        except Exception as e:
            # Logged with the file's place, so the operator can remove it.
            steps.append({"step": "remote_cleanup", "ok": False,
                          "error": f"{rclone_target} could not be removed: {e}"})

        self._cleanup_local(local_path)
        steps.append({"step": "local_cleanup", "ok": True})

        success = all(s.get("ok") for s in steps)
        return {"success": success, "steps": steps, "error": None if success else "One or more steps failed"}

    def _cleanup_local(self, path: object) -> None:
        """Remove a local test file silently."""
        try:
            from pathlib import Path
            Path(str(path)).unlink(missing_ok=True)
        except Exception:
            pass

    async def _get_remote_token(self, remote_name: str) -> tuple[str, str, str]:
        """Read remote type and access token from config. Returns (type, access_token, token_json)."""
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # type: ignore[assignment]
        cfg.read(self.rclone_config_path)
        remote_type = cfg.get(remote_name, "type", fallback="")
        token_str = cfg.get(remote_name, "token", fallback="")
        access_token = ""
        if token_str:
            try:
                token = json.loads(token_str)
                access_token = token.get("access_token", "")
            except json.JSONDecodeError:
                pass
        return remote_type, access_token, token_str

    async def _test_upload(self, remote_name: str, remote_file: str, local_path: object, rclone_target: str) -> bool:
        """Upload a test file to the remote via provider API or rclone."""
        from pathlib import Path

        remote_type, access_token, _ = await self._get_remote_token(remote_name)
        content = Path(str(local_path)).read_bytes()

        if access_token and remote_type == "drive":
            return await self._gdrive_upload(access_token, remote_file, content)
        elif access_token and remote_type == "dropbox":
            return await self._dropbox_upload(access_token, remote_file, content)
        elif access_token and remote_type == "onedrive":
            return await self._onedrive_upload(access_token, remote_file, content)
        else:
            # Fallback to rclone copyto
            try:
                await self._run(
                    ["copyto"], use_config_args=False, timeout=30,
                    positional=[str(local_path), rclone_target],
                )
                return True
            except RcloneError as e:
                logger.warning("test_sync rclone upload failed: %s", e)
                return False

    async def _test_verify(self, remote_name: str, remote_file: str, rclone_target: str) -> bool:
        """Verify a file exists on the remote."""
        remote_type, access_token, _ = await self._get_remote_token(remote_name)

        if access_token and remote_type == "drive":
            return await self._gdrive_file_exists(access_token, remote_file)
        elif access_token and remote_type == "dropbox":
            return await self._dropbox_file_exists(access_token, remote_file)
        elif access_token and remote_type == "onedrive":
            return await self._onedrive_file_exists(access_token, remote_file)
        else:
            try:
                await self._run(
                    ["lsf"], use_config_args=False, timeout=15,
                    positional=[rclone_target],
                )
                return True
            except RcloneError:
                return False

    async def _test_delete_remote(self, remote_name: str, remote_file: str, rclone_target: str) -> None:
        """Delete a test file from the remote."""
        remote_type, access_token, _ = await self._get_remote_token(remote_name)

        if access_token and remote_type == "drive":
            await self._gdrive_delete(access_token, remote_file)
        elif access_token and remote_type == "dropbox":
            await self._dropbox_delete(access_token, remote_file)
        elif access_token and remote_type == "onedrive":
            await self._onedrive_delete(access_token, remote_file)
        else:
            # A failure raises, as the provider-API deletes do: test_sync
            # then reports remote_cleanup as failed (the probe file is left
            # on the remote) instead of a success.
            await self._run(
                ["deletefile"], use_config_args=False, timeout=15,
                positional=[rclone_target],
            )

    # --- Google Drive helpers ---

    async def _gdrive_upload(self, token: str, remote_path: str, content: bytes) -> bool:
        """Upload a file to Google Drive via API."""
        # First ensure parent folder exists / get its ID
        folder_path = "/".join(remote_path.split("/")[:-1])
        filename = remote_path.split("/")[-1]
        folder_id = await self._gdrive_ensure_folder(token, folder_path)
        if not folder_id:
            return False

        metadata = json.dumps({"name": filename, "parents": [folder_id]})
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "metadata": ("metadata", metadata, "application/json"),
                    "file": (filename, content, "text/plain"),
                },
            )
            return resp.status_code == 200

    async def _gdrive_ensure_folder(self, token: str, folder_path: str) -> str | None:
        """Ensure a folder path exists on Google Drive, return its ID."""
        if not folder_path:
            return "root"

        parent_id = "root"
        async with httpx.AsyncClient(timeout=15.0) as client:
            for part in folder_path.split("/"):
                if not part:
                    continue
                # Search for existing folder
                q = f"name='{part}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    return None
                files = resp.json().get("files", [])
                if files:
                    parent_id = files[0]["id"]
                else:
                    # Create folder
                    resp = await client.post(
                        "https://www.googleapis.com/drive/v3/files",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        content=json.dumps({
                            "name": part,
                            "mimeType": "application/vnd.google-apps.folder",
                            "parents": [parent_id],
                        }),
                    )
                    if resp.status_code != 200:
                        return None
                    parent_id = resp.json()["id"]
        return parent_id

    async def _gdrive_file_exists(self, token: str, remote_path: str) -> bool:
        folder_path = "/".join(remote_path.split("/")[:-1])
        filename = remote_path.split("/")[-1]
        folder_id = await self._gdrive_ensure_folder(token, folder_path)
        if not folder_id:
            return False
        q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)",
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code == 200 and len(resp.json().get("files", [])) > 0

    async def _gdrive_delete(self, token: str, remote_path: str) -> None:
        """Delete the probe file; raises ProbeCleanupError unless Drive confirms it is gone.

        The folders are only looked up, never created: a folder that is not
        there means the file is not either. A failed lookup is not proof of
        anything, so it fails the cleanup.
        """
        headers = {"Authorization": f"Bearer {token}"}
        parts = [p for p in remote_path.split("/") if p]
        filename = parts.pop()
        async with httpx.AsyncClient(timeout=10.0) as client:
            parent_id = "root"
            for part in parts:
                q = (f"name='{part}' and '{parent_id}' in parents"
                     " and mimeType='application/vnd.google-apps.folder' and trashed=false")
                resp = await client.get(f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)",
                                        headers=headers)
                if resp.status_code != 200:
                    raise ProbeCleanupError(f"Drive folder lookup answered HTTP {resp.status_code}")
                folders = resp.json().get("files", [])
                if not folders:
                    return  # no folder, so no file in it
                parent_id = folders[0]["id"]
            q = f"name='{filename}' and '{parent_id}' in parents and trashed=false"
            resp = await client.get(f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)",
                                    headers=headers)
            if resp.status_code != 200:
                raise ProbeCleanupError(f"Drive file lookup answered HTTP {resp.status_code}")
            for f in resp.json().get("files", []):
                _check_deleted(
                    await client.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers=headers),
                    "Drive delete",
                )

    # --- Dropbox helpers ---

    async def _dropbox_upload(self, token: str, remote_path: str, content: bytes) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://content.dropboxapi.com/2/files/upload",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Dropbox-API-Arg": json.dumps({"path": f"/{remote_path}", "mode": "overwrite"}),
                    "Content-Type": "application/octet-stream",
                },
                content=content,
            )
            return resp.status_code == 200

    async def _dropbox_file_exists(self, token: str, remote_path: str) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.dropboxapi.com/2/files/get_metadata",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps({"path": f"/{remote_path}"}),
            )
            return resp.status_code == 200

    async def _dropbox_delete(self, token: str, remote_path: str) -> None:
        """Delete the probe file; raises ProbeCleanupError unless Dropbox confirms it is gone."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.dropboxapi.com/2/files/delete_v2",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps({"path": f"/{remote_path}"}),
            )
        if resp.status_code == 409 and _dropbox_error(resp).startswith("path_lookup/not_found"):
            return  # Dropbox's "not found": already gone
        _check_deleted(resp, "Dropbox delete")

    # --- OneDrive helpers ---

    async def _onedrive_upload(self, token: str, remote_path: str, content: bytes) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"https://graph.microsoft.com/v1.0/me/drive/root:/{remote_path}:/content",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
                content=content,
            )
            return resp.status_code in (200, 201)

    async def _onedrive_file_exists(self, token: str, remote_path: str) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/drive/root:/{remote_path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code == 200

    async def _onedrive_delete(self, token: str, remote_path: str) -> None:
        """Delete the probe file; raises ProbeCleanupError unless OneDrive confirms it is gone."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"https://graph.microsoft.com/v1.0/me/drive/root:/{remote_path}",
                headers={"Authorization": f"Bearer {token}"},
            )
        _check_deleted(resp, "OneDrive delete")


def _dropbox_error(resp: httpx.Response) -> str:
    """The error_summary of a Dropbox error answer ("" when there is none)."""
    try:
        summary = resp.json().get("error_summary", "")
    except (ValueError, AttributeError):
        return ""
    return summary if isinstance(summary, str) else ""
