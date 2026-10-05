"""Restore and delete in a profile's trash: refusals, odd names and partial failures.

The trash holds the only copy of what a sync replaced or deleted, so every
case here checks the files on disk afterwards: a refused or failed entry
stays in the trash, a file in the way is kept, nothing outside the synced
folder is touched. The local side runs on real files, the remote side
through the real rclone binary with a `local`-type remote in a temp
rclone.conf (as in test_sync_features_integration.py).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.api.schemas import TrashSide
from backend.exceptions import RcloneAuthError, RcloneError
from backend.services import trash as trash_module
from backend.services.rclone import TRASH_DIR, RcloneService
from backend.services.sync_engine import TRASH_STAMP_FORMAT
from backend.services.trash import (
    LocalTrash,
    RemoteTrash,
    TrashPathError,
    apply_to_trash,
    check_trash_id,
    list_trash,
)

if shutil.which("rclone") is None and os.environ.get("OMNISYNC_REQUIRE_RCLONE") == "1":
    raise RuntimeError("OMNISYNC_REQUIRE_RCLONE=1 but rclone is not on PATH")
needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")

STAMP = "2026-01-02T03-04-05Z"
NOW = "2026-03-04T05-06-07Z"  # the stamp a restore moves a replaced file under
OLD = time.time() - 7200

# Names a sync can trash: unicode, spaces, option-like and the longest a
# file system allows (255 bytes).
ODD_NAMES = ["Grüße ñ 文件.txt", "with  spaces .txt", "-rf", "--help", "x" * 251 + ".txt"]


def write(root: Path, rel: str, content: str, mtime: float | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def trashed(root: Path, rel: str, content: str, stamp: str = STAMP, mtime: float | None = OLD) -> str:
    """Put a file into root's trash as a sync would; its trash id."""
    write(root, f"{TRASH_DIR}/{stamp}/{rel}", content, mtime)
    return f"{stamp}/{rel}"


def trash_files(root: Path) -> dict[str, str]:
    trash = root / TRASH_DIR
    return {str(p.relative_to(trash)): p.read_text() for p in sorted(trash.rglob("*"))
            if p.is_file() and not p.is_symlink()}


@pytest.fixture
def local(tmp_path: Path) -> Path:
    folder = tmp_path / "local"
    folder.mkdir()
    return folder


async def act(action: str, side: TrashSide, ids: list[str], root: Path, overwrite: bool = False,
              rclone: RcloneService | None = None, remote_dir: str = "") -> tuple[list[str], dict[str, str]]:
    """apply_to_trash with the stamp fixed; (done, {failed id: code})."""
    done, failed = await apply_to_trash(action, side, ids, overwrite, str(root), remote_dir,
                                        rclone or RcloneService(rclone_config_path="/nonexistent"))
    return done, {f.id: f.code for f in failed}


@pytest.fixture(autouse=True)
def fixed_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trash_module, "_now_stamp", lambda: NOW)


# --- ids ---


@pytest.mark.parametrize("bad", [
    "", "/abs/a.txt", "..", "../a.txt", "a/../../b", "a/./b", "./a", "a//b", "a/", "a/b/",
    "a\x00b", "a\nb", "a/..", ".",
])
def test_ids_that_are_not_plain_paths_are_refused(bad: str) -> None:
    """Every way out of the trash (or an ambiguous name) is refused before any file is looked at."""
    with pytest.raises(TrashPathError):
        check_trash_id(bad)


@pytest.mark.parametrize("good", [f"{STAMP}/{name}" for name in ODD_NAMES] + ["file-at-top", "a/b c/-d"])
def test_odd_but_plain_ids_are_accepted(good: str) -> None:
    """Unicode, spaces and option-like names are ordinary files, not refusals."""
    assert check_trash_id(good) == good


# --- local listing ---


async def test_no_trash_folder_lists_nothing_and_acts_on_nothing(local: Path) -> None:
    """A profile that never trashed anything: empty listing, every id not_found."""
    listing = await list_trash(TrashSide.LOCAL, str(local), "", RcloneService(rclone_config_path="/nonexistent"))
    assert (listing.entries, listing.total_files, listing.truncated) == ([], 0, False)
    for action in ("restore", "delete"):
        done, failed = await act(action, TrashSide.LOCAL, [f"{STAMP}/a.txt"], local)
        assert done == [] and failed == {f"{STAMP}/a.txt": "not_found"}


async def test_symlinked_trash_folder_is_never_followed(local: Path, tmp_path: Path) -> None:
    """A .omnisync-trash that is a symlink elsewhere is treated as no trash at all."""
    outside = tmp_path / "outside"
    write(outside, f"{STAMP}/victim.txt", "keep me")
    (local / TRASH_DIR).symlink_to(outside)
    assert LocalTrash(str(local)).list().total_files == 0
    for action in ("delete", "restore"):
        done, failed = await act(action, TrashSide.LOCAL, [f"{STAMP}/victim.txt"], local)
        assert failed == {f"{STAMP}/victim.txt": "not_found"}
    assert (outside / STAMP / "victim.txt").read_text() == "keep me"
    assert not (local / "victim.txt").exists()


def test_listing_is_capped_but_totals_count_everything(local: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A huge trash lists MAX_LISTED entries, newest folder first, and says it is truncated."""
    monkeypatch.setattr(trash_module, "MAX_LISTED", 2)
    newer = "2026-05-05T00-00-00Z"
    trashed(local, "b.txt", "bb", stamp=newer)
    trashed(local, "a.txt", "a", stamp=newer)
    trashed(local, "old.txt", "oldest")
    write(local, f"{TRASH_DIR}/loose.txt", "xyz")  # a file directly in the trash
    listing = LocalTrash(str(local)).list()
    assert [e.id for e in listing.entries] == ["loose.txt", f"{newer}/a.txt"]
    assert listing.total_files == 4 and listing.total_bytes == 2 + 1 + 6 + 3
    assert listing.truncated is True
    loose = listing.entries[0]
    assert (loose.folder, loose.path, loose.trashed_at) == ("", "loose.txt", None)


def test_listing_survives_a_file_vanishing_meanwhile(local: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file a running cleanup removes between the folder scan and its stat is skipped, not an error."""
    trashed(local, "stays.txt", "s")
    gone = trashed(local, "gone.txt", "g")
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if str(path).endswith(gone):
            raise FileNotFoundError(path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(trash_module.os, "lstat", lstat)
    listing = LocalTrash(str(local)).list()
    assert [e.path for e in listing.entries] == ["stays.txt"] and listing.total_files == 1


# --- local restore and delete: odd names ---


@pytest.mark.parametrize("name", ODD_NAMES)
async def test_odd_names_restore_and_delete_locally(local: Path, name: str) -> None:
    """Unicode, spaces, a leading '-' and a 255-byte name restore to their place and delete for good."""
    entry = trashed(local, f"sub dir/{name}", "content")
    assert [e.id for e in LocalTrash(str(local)).list().entries] == [entry]
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local)
    assert (done, failed) == ([entry], {})
    assert (local / "sub dir" / name).read_text() == "content"
    assert not (local / TRASH_DIR / STAMP).exists()  # emptied folders are pruned ...
    assert (local / TRASH_DIR).is_dir()  # ... but not the trash itself

    entry = trashed(local, name, "again")
    done, failed = await act("delete", TrashSide.LOCAL, [entry], local)
    assert (done, failed) == ([entry], {})
    assert trash_files(local) == {}
    assert (local / "sub dir" / name).read_text() == "content"


async def test_restore_recreates_missing_folders(local: Path) -> None:
    """The file's folders were deleted along with it: they are made again."""
    entry = trashed(local, "a/b/c/deep.txt", "deep")
    done, _ = await act("restore", TrashSide.LOCAL, [entry], local)
    assert done == [entry] and (local / "a/b/c/deep.txt").read_text() == "deep"


# --- local refusals ---


async def test_restore_into_the_trash_is_refused(local: Path) -> None:
    """An entry whose path starts with the trash folder cannot be restored over other trashed files."""
    victim = trashed(local, "keep.txt", "kept")
    entry = trashed(local, f"{TRASH_DIR}/{victim}", "intruder")
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local)
    assert done == [] and failed == {entry: "invalid"}
    assert trash_files(local) == {victim: "kept", entry: "intruder"}


async def test_restore_through_a_symlink_out_of_the_folder_is_refused(local: Path, tmp_path: Path) -> None:
    """A symlinked folder in the synced folder cannot carry a restore outside it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (local / "out").symlink_to(outside)
    entry = trashed(local, "out/x.txt", "x")
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local)
    assert failed == {entry: "invalid"}
    assert list(outside.iterdir()) == [] and trash_files(local) == {entry: "x"}


async def test_restore_through_a_symlink_into_the_trash_is_refused(local: Path) -> None:
    """A symlink to the trash folder cannot be used to restore onto trashed files."""
    victim = trashed(local, "a.txt", "old version")
    (local / "t").symlink_to(local / TRASH_DIR / STAMP)
    entry = trashed(local, "t/a.txt", "intruder", stamp="2026-02-02T00-00-00Z")
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local, overwrite=True)
    assert failed == {entry: "invalid"}
    assert trash_files(local)[victim] == "old version"


async def test_symlinked_folder_inside_the_trash_is_refused(local: Path, tmp_path: Path) -> None:
    """A folder inside the trash that is a symlink elsewhere: neither delete nor restore reaches through it."""
    outside = tmp_path / "outside"
    write(outside, "victim.txt", "keep me")
    (local / TRASH_DIR).mkdir()
    (local / TRASH_DIR / STAMP).symlink_to(outside)
    for action in ("delete", "restore"):
        done, failed = await act(action, TrashSide.LOCAL, [f"{STAMP}/victim.txt"], local)
        assert failed == {f"{STAMP}/victim.txt": "invalid"}
    assert (outside / "victim.txt").read_text() == "keep me"


async def test_a_trashed_folder_is_not_an_entry(local: Path) -> None:
    """Ids name files; a folder id is not_found rather than deleted with all it holds."""
    trashed(local, "folder/inner.txt", "inner")
    for action in ("delete", "restore"):
        done, failed = await act(action, TrashSide.LOCAL, [f"{STAMP}/folder"], local)
        assert failed == {f"{STAMP}/folder": "not_found"}
    assert trash_files(local) == {f"{STAMP}/folder/inner.txt": "inner"}


async def test_deleting_a_trashed_symlink_removes_only_the_link(local: Path, tmp_path: Path) -> None:
    """A symlink a sync trashed is deleted as a link; the file it points to stays."""
    target = write(tmp_path, "outside/target.txt", "precious")
    (local / TRASH_DIR / STAMP).mkdir(parents=True)
    (local / TRASH_DIR / STAMP / "link").symlink_to(target)
    assert [e.id for e in LocalTrash(str(local)).list().entries] == [f"{STAMP}/link"]
    done, _ = await act("delete", TrashSide.LOCAL, [f"{STAMP}/link"], local)
    assert done == [f"{STAMP}/link"]
    assert target.read_text() == "precious"
    assert not (local / TRASH_DIR / STAMP).exists()


async def test_folder_in_the_way_is_refused(local: Path) -> None:
    """A folder now has the file's name: neither the folder nor the trashed file moves."""
    entry = trashed(local, "docs", "a file once")
    write(local, "docs/inner.txt", "inner")
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local, overwrite=True)
    assert failed == {entry: "target_is_folder"}
    assert (local / "docs/inner.txt").read_text() == "inner"
    assert trash_files(local) == {entry: "a file once"}


async def test_newer_file_in_the_way_needs_confirmation_and_is_then_kept(local: Path) -> None:
    """A newer current file is only replaced with overwrite, and then goes to the trash, not away."""
    entry = trashed(local, "a.txt", "old")
    write(local, "a.txt", "newer")
    done, failed = await act("restore", TrashSide.LOCAL, [entry], local)
    assert failed == {entry: "target_newer"}
    assert (local / "a.txt").read_text() == "newer" and trash_files(local) == {entry: "old"}

    done, failed = await act("restore", TrashSide.LOCAL, [entry], local, overwrite=True)
    assert (done, failed) == ([entry], {})
    assert (local / "a.txt").read_text() == "old"
    assert trash_files(local) == {f"{NOW}/a.txt": "newer"}


async def test_older_file_in_the_way_is_replaced_and_kept(local: Path) -> None:
    """The trashed version is newer: restored without asking, the current one goes to the trash."""
    entry = trashed(local, "a.txt", "trashed", mtime=time.time())
    write(local, "a.txt", "older", mtime=OLD - 3600)
    done, _ = await act("restore", TrashSide.LOCAL, [entry], local)
    assert done == [entry]
    assert (local / "a.txt").read_text() == "trashed"
    assert trash_files(local) == {f"{NOW}/a.txt": "older"}


async def test_no_free_trash_folder_name_refuses_without_moving(local: Path) -> None:
    """When every later stamp is taken, the restore stops before touching either file."""
    entry = trashed(local, "a.txt", "old")
    write(local, "a.txt", "current")
    start = datetime.strptime(NOW, TRASH_STAMP_FORMAT)
    for seconds in range(3600):
        stamp = (start + timedelta(seconds=seconds)).strftime(TRASH_STAMP_FORMAT)
        write(local, f"{TRASH_DIR}/{stamp}/a.txt", "taken")
    with pytest.raises(RuntimeError, match="no free trash folder"):
        await act("restore", TrashSide.LOCAL, [entry], local, overwrite=True)
    assert (local / "a.txt").read_text() == "current"
    assert (local / TRASH_DIR / entry).read_text() == "old"


# --- local partial failures ---


async def test_one_failure_never_stops_the_rest(local: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A batch with a missing, an invalid and a blocked entry still restores the good ones, each once."""
    good = trashed(local, "good.txt", "good")
    blocked = trashed(local, "file/child.txt", "child")
    write(local, "file", "a file where a folder must go")  # makedirs fails: OSError
    other = trashed(local, "other.txt", "other")
    ids = [good, f"{STAMP}/missing.txt", "../escape", blocked, other, good]
    with caplog.at_level(logging.WARNING, logger="backend.services.trash"):
        done, failed = await act("restore", TrashSide.LOCAL, ids, local)
    assert done == [good, other]
    assert failed == {f"{STAMP}/missing.txt": "not_found", "../escape": "invalid", blocked: "failed"}
    assert (local / "good.txt").read_text() == "good" and (local / "other.txt").read_text() == "other"
    assert (local / "file").read_text() == "a file where a folder must go"
    assert trash_files(local) == {blocked: "child"}  # the failed one is still there
    assert "Trash restore of" in caplog.text


# --- remote side, through real rclone ---


@pytest.fixture
def remote(tmp_path: Path) -> tuple[RcloneService, Path, str]:
    folder = tmp_path / "remote"
    folder.mkdir()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[testremote]\ntype = local\n")
    return RcloneService(rclone_config_path=str(conf)), folder, f"testremote:{folder}"


async def remote_act(remote, action: str, ids: list[str], overwrite: bool = False):
    rclone, _folder, remote_dir = remote
    return await act(action, TrashSide.REMOTE, ids, Path("/nonexistent"), overwrite, rclone, remote_dir)


@needs_rclone
async def test_remote_without_trash_folder_lists_nothing(remote) -> None:
    """rclone's 'directory not found' for a missing trash is an empty trash, not an error."""
    rclone, _folder, remote_dir = remote
    listing = await list_trash(TrashSide.REMOTE, "/nonexistent", remote_dir, rclone)
    assert listing.side == TrashSide.REMOTE and listing.total_files == 0


@needs_rclone
async def test_remote_listing_errors_are_not_hidden(remote) -> None:
    """Any other rclone failure (here: an unknown remote) is raised, never shown as an empty trash."""
    rclone, folder, _remote_dir = remote
    with pytest.raises(RcloneError):
        await RemoteTrash(rclone, f"unknownremote:{folder}").list()


@needs_rclone
@pytest.mark.parametrize("name", ODD_NAMES)
async def test_odd_names_restore_and_delete_on_the_remote(remote, name: str) -> None:
    """Names rclone could read as options or that need quoting go to the right file on the remote."""
    _rclone, folder, _remote_dir = remote
    entry = trashed(folder, f"sub dir/{name}", "content")
    listing = await RemoteTrash(remote[0], remote[2]).list()
    assert [e.id for e in listing.entries] == [entry] and listing.entries[0].modified is not None
    done, failed = await remote_act(remote, "restore", [entry])
    assert (done, failed) == ([entry], {})
    assert (folder / "sub dir" / name).read_text() == "content"

    entry = trashed(folder, name, "again")
    done, failed = await remote_act(remote, "delete", [entry])
    assert (done, failed) == ([entry], {})
    assert trash_files(folder) == {}
    assert (folder / "sub dir" / name).read_text() == "content"


@needs_rclone
async def test_remote_refusals_leave_everything_in_place(remote) -> None:
    """Invalid ids, ids into the trash and entries no longer there change nothing on the remote."""
    _rclone, folder, _remote_dir = remote
    victim = trashed(folder, "keep.txt", "kept")
    into_trash = trashed(folder, f"{TRASH_DIR}/{victim}", "intruder")
    done, failed = await remote_act(remote, "restore", ["../x", into_trash, f"{STAMP}/missing.txt"])
    assert done == [] and failed == {"../x": "invalid", into_trash: "invalid", f"{STAMP}/missing.txt": "not_found"}
    done, failed = await remote_act(remote, "delete", ["/abs", f"{STAMP}/missing.txt"])
    assert done == [] and failed == {"/abs": "invalid", f"{STAMP}/missing.txt": "not_found"}
    assert trash_files(folder) == {victim: "kept", into_trash: "intruder"}


@needs_rclone
async def test_remote_newer_file_needs_confirmation_and_is_then_kept(remote) -> None:
    """Same rule as locally: the newer remote file is replaced only with overwrite, and kept in the trash."""
    _rclone, folder, _remote_dir = remote
    entry = trashed(folder, "a.txt", "old")
    write(folder, "a.txt", "newer")
    done, failed = await remote_act(remote, "restore", [entry])
    assert failed == {entry: "target_newer"} and (folder / "a.txt").read_text() == "newer"
    done, failed = await remote_act(remote, "restore", [entry], overwrite=True)
    assert done == [entry] and (folder / "a.txt").read_text() == "old"
    assert trash_files(folder) == {f"{NOW}/a.txt": "newer"}


@needs_rclone
async def test_remote_folder_in_the_way_fails_without_loss(remote, caplog: pytest.LogCaptureFixture) -> None:
    """rclone refuses to move a file onto a folder: the entry fails, the folder and the trashed file stay."""
    _rclone, folder, _remote_dir = remote
    entry = trashed(folder, "docs", "a file once")
    write(folder, "docs/inner.txt", "inner")
    good = trashed(folder, "fine.txt", "fine")
    with caplog.at_level(logging.WARNING, logger="backend.services.trash"):
        done, failed = await remote_act(remote, "restore", [entry, good], overwrite=True)
    assert done == [good] and failed == {entry: "failed"}
    assert (folder / "docs/inner.txt").read_text() == "inner"
    assert trash_files(folder) == {entry: "a file once"}
    assert "Trash restore of" in caplog.text


# --- remote: what rclone answers, and its errors ---


class ScriptedRclone(RcloneService):
    """An RcloneService whose listing and moves are scripted (to reach answers real rclone never gives)."""

    def __init__(self, items: list | None = None, stat_error: Exception | None = None) -> None:
        super().__init__(rclone_config_path="/nonexistent")
        self.items = items or []
        self.stat_error = stat_error
        self.deleted: list[str] = []

    async def lsjson(self, path: str, rclone_filter: list[str] | None = None) -> list[dict]:
        return self.items

    async def lsjson_paths(self, root: str, file_paths: list[str]) -> dict[str, dict]:
        if self.stat_error is not None:
            raise self.stat_error
        return {p: {"Path": p, "Size": 1} for p in file_paths}

    async def delete_file(self, path: str):  # type: ignore[override]
        self.deleted.append(path)


async def test_unusable_remote_metadata_is_listed_without_it() -> None:
    """Folders and malformed rows are skipped; a bad size or time shows as unknown, not as an error."""
    rclone = ScriptedRclone(items=[
        {"Path": f"{STAMP}/ok.txt", "Size": 3, "ModTime": "2026-01-02T03:04:05.000000000Z"},
        {"Path": f"{STAMP}/bad-time.txt", "Size": -1, "ModTime": "yesterday"},
        {"Path": f"{STAMP}/no-time.txt", "Size": "3", "ModTime": 12},
        {"Path": f"{STAMP}/folder", "IsDir": True},
        {"Path": 42}, "not a row",
    ])
    listing = await RemoteTrash(rclone, "r:docs").list()
    by_path = {e.path: e for e in listing.entries}
    assert sorted(by_path) == ["bad-time.txt", "no-time.txt", "ok.txt"]
    assert by_path["ok.txt"].size == 3 and by_path["ok.txt"].modified is not None
    assert (by_path["bad-time.txt"].size, by_path["bad-time.txt"].modified) == (None, None)
    assert (by_path["no-time.txt"].size, by_path["no-time.txt"].modified) == (None, None)
    assert listing.total_bytes == 3


async def test_remote_errors_fail_one_entry_but_auth_errors_stop_the_batch(caplog) -> None:
    """A provider error fails its entry with a generic message; a sign-in failure is raised for the route."""
    leak = "stderr: token=secret-value /home/someone"
    rclone = ScriptedRclone(stat_error=RcloneError(leak))
    with caplog.at_level(logging.WARNING, logger="backend.services.trash"):
        done, failed = await apply_to_trash("delete", TrashSide.REMOTE, [f"{STAMP}/a", f"{STAMP}/b"], False,
                                            "/l", "r:docs", rclone)
    assert done == [] and [f.code for f in failed] == ["failed", "failed"]
    assert all("secret-value" not in f.message and "log" in f.message for f in failed)
    assert "Trash delete of" in caplog.text and "/home/someone" in caplog.text  # details: server log only

    rclone = ScriptedRclone(stat_error=RcloneAuthError("token expired"))
    with pytest.raises(RcloneAuthError):
        await apply_to_trash("delete", TrashSide.REMOTE, [f"{STAMP}/a"], False, "/l", "r:docs", rclone)
    assert rclone.deleted == []

    rclone = ScriptedRclone()
    rclone.lsjson = _raise_auth  # type: ignore[method-assign]
    with pytest.raises(RcloneAuthError):
        await list_trash(TrashSide.REMOTE, "/l", "r:docs", rclone)


async def _raise_auth(path: str, rclone_filter: list[str] | None = None) -> list[dict]:
    raise RcloneAuthError("token expired")
