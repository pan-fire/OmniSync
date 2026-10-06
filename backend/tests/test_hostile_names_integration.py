"""Hostile file names through every sync mode, against the real rclone binary.

People's folders hold names written on other systems: decomposed Unicode
from macOS, right-to-left text, emoji, names that look like options,
comments or patterns to rclone, control characters, bytes that are not
UTF-8, names at the 255-byte limit. Each mode (push, pull, two-way and the
per-file actions) must carry every such file over byte for byte, keep the
replaced versions in the trash under the same names, and leave no rclone
.partial file behind. Then the cases with a defined behaviour of their
own: names equal after Unicode normalisation, case-only renames, symbolic
links, empty files and folders, and files that change during a sync. Where
rclone cannot carry a name as it is, OmniSync says so: a warning in the
preview, the diff and the job (see backend/services/sync_engine/names.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.api.routes.jobs import job_warnings
from backend.api.schemas import ChangeCategory, FileAction, SelectiveSyncItem, SyncWarning, SyncWarningCode
from backend.services.rclone import PARTIAL_NAME, SENTINEL_FILE, TRASH_DIR
from backend.services.rclone.bisync_names import LONGEST_SUFFIX, NAME_MAX, bisync_names_fit, bisync_session_name
from backend.services.sync_engine import SyncEngine, bisync_workdir
from backend.services.sync_engine.selective import LOCAL_SYMLINK, NAME_NOT_UTF8
from backend.tests.real_rclone import Env, empty_dirs, make_env, needs_rclone, partials, tree, write

pytestmark = needs_rclone

NFC, NFD = (lambda s: unicodedata.normalize("NFC", s)), (lambda s: unicodedata.normalize("NFD", s))

HOSTILE = {
    "nfc": NFC("résumé.txt"),
    "nfd": NFD("naïve café.txt"),
    "arabic": "تقرير المبيعات.txt",
    "hebrew-folder": "מסמכים/שלום.txt",
    "bidi-override": "invoice‮gpj.exe",
    "emoji": "📁 plans/🎉 party.txt",
    "zwj-emoji": "👨‍👩‍👧 family.txt",
    "spaces": " leading and trailing .txt ",
    "spaces-folder": "   /inside.txt",
    "dash": "-rf.txt",
    "dash-folder": "--help/--version.txt",
    "hash": "#notes.txt",
    "hash-folder": "#tags/#1.txt",
    "percent": "100% done %20 %s.txt",
    "glob": "a[1]{2}*?.txt",
    "backslash": "back\\slash.txt",
    "quotes": "it's \"quoted\".txt",
    "newline": "line\nbreak.txt",
    "tab-cr": "tab\tand\rcr.txt",
    "control": "ctrl\x01\x1b\x7f.txt",
    # The characters rclone's local backend encodes control characters as.
    "control-pictures": "␀ and ␊.txt",
    "dots": "...",
    "trailing-dot": "end.",
    "trash-lookalike": TRASH_DIR + ".txt",
    "trash-in-subfolder": f"sub/{TRASH_DIR}/kept.txt",
    "partial-lookalike": "notes.partial",
    "max-ascii": "L" * (NAME_MAX - 4) + ".txt",
    "max-utf8": "é" * 125 + "x.txt",            # 255 bytes, 130 characters
    "max-emoji": "😀" * 62 + "x.txt",           # 253 bytes
    "deep": "/".join(["d" * 200] * 15) + "/deep.txt",
}
assert all(len(os.fsencode(part)) <= NAME_MAX for name in HOSTILE.values() for part in name.split("/"))
assert len({NFC(n) for n in HOSTILE.values()}) == len(HOSTILE)

MODES = ["push", "pull", "two_way", "per_file"]


def content(name: str, round_: int) -> bytes:
    return f"{round_}:".encode() + hashlib.sha256(os.fsencode(name)).digest()


def source(env: Env, mode: str) -> Path:
    return env.remote if mode == "pull" else env.local


async def sync(env: Env, engine: SyncEngine, mode: str) -> int:
    """One run of ``mode``; the per-file actions push or pull every file of a fresh diff. Returns the job id."""
    if mode == "push":
        job_id = await engine.push()
    elif mode == "pull":
        job_id = await engine.pull()
    elif mode == "two_way":
        job_id = await engine.two_way_sync()
    else:
        diff = await engine.enhanced_diff()
        assert diff.error is None, diff.error
        result = await engine.selective_sync([
            SelectiveSyncItem(path=f.path, action=FileAction.PULL if f.category in (
                ChangeCategory.REMOTE_ONLY, ChangeCategory.MODIFIED_REMOTE) else FileAction.PUSH)
            for f in diff.files
        ])
        assert result.failed == 0, result.errors
        job_id = result.job_id
    assert job_id is not None
    return job_id


def engine_for(env: Env, mode: str) -> SyncEngine:
    return env.engine(sync_mode="two_way" if mode == "two_way" else "mirror")


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    env, db = await make_env(tmp_path, monkeypatch)
    yield env
    for engine in env.engines:
        await engine.stop()
    await db.dispose()


def trash(root: Path) -> dict[str, str]:
    """Path inside the timestamp folder -> digest, for everything in root's trash."""
    prefix = TRASH_DIR + os.sep
    return {k.split(os.sep, 2)[2]: v for k, v in tree(root, trash=True).items() if k.startswith(prefix)}


# --- Every hostile name, every mode ---


@pytest.mark.parametrize("mode", MODES)
async def test_hostile_names_arrive_byte_for_byte(env: Env, mode: str):
    """Created, then replaced: both sides end identical, the old versions in the trash, same names."""
    side = source(env, mode)
    old = time.time() - 3600
    for name in HOSTILE.values():
        write(side, name, content(name, 1), mtime=old)
    engine = engine_for(env, mode)

    job = await env.completed(await sync(env, engine, mode))
    assert job.files_changed == len(HOSTILE)
    assert job_warnings(job) == []  # odd, but valid names: nothing to warn about
    assert tree(env.local) == tree(env.remote)
    assert set(tree(side)) == set(HOSTILE.values())
    assert partials(env.local, env.remote) == []

    for name in HOSTILE.values():
        write(side, name, content(name, 2))
    job = await env.completed(await sync(env, engine, mode))
    assert job.files_changed == len(HOSTILE)
    after = tree(env.local)
    assert after == tree(env.remote)
    assert after == {name: hashlib.blake2b(content(name, 2), digest_size=16).hexdigest() for name in HOSTILE.values()}
    # The replaced versions are in the other side's trash, under their own names.
    other = env.local if side == env.remote else env.remote
    assert trash(other) == {name: hashlib.blake2b(content(name, 1), digest_size=16).hexdigest()
                            for name in HOSTILE.values()}
    assert partials(env.local, env.remote) == []


# --- Generated names (Hypothesis) ---

# Any byte string a Linux file name can be, mostly invalid UTF-8, or any
# text; minus the names OmniSync itself owns at the top of the folder.
name_bytes = st.one_of(
    st.binary(min_size=1, max_size=40),
    st.text(st.characters(exclude_categories=("Cs",)), min_size=1, max_size=40).map(os.fsencode),
).filter(lambda b: b"/" not in b and b"\0" not in b and b not in (b".", b"..") and len(b) <= NAME_MAX)
names = st.lists(name_bytes.map(os.fsdecode), min_size=1, max_size=6).map(
    # rclone treats names equal after NFC normalisation as one file: see
    # test_names_equal_after_normalisation_are_reported_and_lose_nothing.
    lambda ns: list({NFC(n): n for n in ns}.values())
).filter(lambda ns: not {SENTINEL_FILE, TRASH_DIR} & set(ns) and not any(PARTIAL_NAME.fullmatch(n) for n in ns))


@pytest.mark.parametrize("mode", ["push", "two_way"])
@settings(max_examples=12, derandomize=True, database=None)
@given(files=names)
def test_generated_names_arrive_byte_for_byte(mode: str, files: list[str]):
    async def run() -> None:
        with tempfile.TemporaryDirectory(prefix="osync-names-") as tmp, pytest.MonkeyPatch.context() as mp:
            env, db = await make_env(Path(tmp), mp)
            try:
                for i, name in enumerate(files):
                    write(env.local, f"d{i}/{name}", content(name, 1))
                    write(env.local, name, content(name, 1))
                engine = engine_for(env, mode)
                job = await env.completed(await sync(env, engine, mode))
                assert job.files_changed == 2 * len(files)
                assert tree(env.local) == tree(env.remote)
                assert len(tree(env.local)) == 2 * len(files)
                assert partials(env.local, env.remote) == []
            finally:
                for engine in env.engines:
                    await engine.stop()
                await db.dispose()

    asyncio.run(run())


# --- Names equal after Unicode normalisation ---


def warning(code: SyncWarningCode, *paths: str) -> SyncWarning:
    return SyncWarning(code=code, count=len(paths), paths=list(paths))


@pytest.mark.parametrize("mode", ["push", "two_way"])
async def test_names_equal_after_normalisation_are_reported_and_lose_nothing(env: Env, mode: str):
    """``café.txt`` composed (NFC) and decomposed (NFD), side by side in one folder.

    rclone compares names after Unicode normalisation, so it carries only
    one of the two to the other side ("Duplicate object found in source").
    The preview, the diff and every job say so, naming the file; the run
    is "completed" only together with that warning. Nothing may be lost on
    the way: both local files keep their content through every run, each
    version a run replaces on the remote is in the remote's trash, and the
    other spelling follows once it is alone.
    """
    nfc, nfd = NFC("café.txt"), NFD("café.txt")
    write(env.local, nfc, "composed")
    write(env.local, nfd, "decomposed")
    write(env.local, "other.txt", "other")
    engine = engine_for(env, mode)
    collision = [warning(SyncWarningCode.NAME_COLLISION, nfc)]

    assert (await engine.preview_sync()).warnings == collision
    assert (await engine.enhanced_diff()).warnings == collision

    async def run_keeps_local(expected: dict[str, str]) -> tuple[str, list[SyncWarning]]:
        job = await env.job(await sync(env, engine, mode))
        for name, text in expected.items():
            assert (env.local / name).read_text() == text, name
        return job.status, job_warnings(job)

    assert await run_keeps_local({nfc: "composed", nfd: "decomposed"}) == ("completed", collision)
    remote = {os.fsencode(n): (env.remote / n).read_text() for n in os.listdir(env.remote) if n.startswith("caf")}
    assert len(remote) == 1 and set(remote.values()) <= {"composed", "decomposed"}

    (env.local / nfd).write_text("decomposed, edited")
    assert (await run_keeps_local({nfc: "composed", nfd: "decomposed, edited"}))[1] == collision

    # Alone, the decomposed name syncs, without a warning: no version of either file is gone.
    (env.local / nfc).unlink()
    if mode == "two_way" and engine._state.resync_required:
        await env.completed(await engine.resync())
    await run_keeps_local({nfd: "decomposed, edited"})
    if mode == "two_way" and engine._state.resync_required:
        await env.completed(await engine.resync())
        await run_keeps_local({nfd: "decomposed, edited"})
    on_remote = {n: (env.remote / n).read_text() for n in os.listdir(env.remote) if n.startswith("caf")}
    assert on_remote == {nfd: "decomposed, edited"}
    # The composed file the user deleted is still in the remote's trash.
    assert hashlib.blake2b(b"composed", digest_size=16).hexdigest() in trash(env.remote).values()
    assert (await engine.preview_sync()).warnings == []


async def test_folders_equal_after_normalisation_are_reported(env: Env):
    """Two folders ``dé`` (NFC and NFD): rclone syncs one folder and drops
    the other with everything in it, so the warning names the folder."""
    write(env.local, NFC("dé") + "/a.txt", "a")
    write(env.local, NFD("dé") + "/b.txt", "b")
    engine = env.engine()

    job = await env.completed(await engine.push())

    assert job_warnings(job) == [warning(SyncWarningCode.NAME_COLLISION, NFC("dé") + "/")]
    assert len(tree(env.remote)) == 1
    assert len(tree(env.local)) == 2


async def test_a_collision_the_profile_filters_out_is_not_reported(env: Env):
    """Only what the sync sees counts: an excluded folder's names are no concern."""
    write(env.local, "skip/" + NFC("é.txt"), "1")
    write(env.local, "skip/" + NFD("é.txt"), "2")
    write(env.local, "keep.txt", "k")
    engine = env.engine(rclone_filter=["- /skip/**"])

    assert (await engine.preview_sync()).warnings == []
    job = await env.completed(await engine.push())
    assert job_warnings(job) == []


# --- Case-only renames ---


@pytest.mark.parametrize("mode", MODES)
async def test_case_only_rename(env: Env, mode: str):
    """``Report.txt`` renamed to ``report.txt``.

    Push, pull and two-way: one file under the new name on both sides, the
    old one in the other side's trash. The per-file actions only copy, never
    delete: the old name is copied back from the other side, so both names
    end up on both sides and nothing goes to the trash.
    """
    side = source(env, mode)
    other = env.local if side == env.remote else env.remote
    write(side, "Report.txt", "quarterly figures", mtime=time.time() - 3600)
    engine = engine_for(env, mode)
    await env.completed(await sync(env, engine, mode))

    (side / "Report.txt").rename(side / "report.txt")
    await env.completed(await sync(env, engine, mode))

    expected = ["Report.txt", "report.txt"] if mode == "per_file" else ["report.txt"]
    for root in (env.local, env.remote):
        assert sorted(n for n in os.listdir(root) if n.lower() == "report.txt") == expected, root
        assert all((root / n).read_text() == "quarterly figures" for n in expected)
    assert trash(other) == ({} if mode == "per_file" else {
        "Report.txt": hashlib.blake2b(b"quarterly figures", digest_size=16).hexdigest()})


# --- Names that are not UTF-8 ---


BAD = os.fsdecode(b"bad\xff\xfe.txt")


# How the warnings show it: each bad byte escaped.
BAD_SHOWN = "bad\\xff\\xfe.txt"


@pytest.mark.parametrize("mode", ["push", "pull"])
async def test_name_that_is_not_utf8_mirrors_byte_for_byte(env: Env, mode: str):
    """Whole-folder syncs carry it; once it is in the local folder (a run
    checks the folder as it starts), every run reports it all the same, as
    the per-file actions cannot act on it."""
    side = source(env, mode)
    write(side, BAD, "v1", mtime=time.time() - 3600)
    engine = engine_for(env, mode)
    for version in ("v1", "v2"):
        was_local = (env.local / BAD).exists()
        write(side, BAD, version)
        job = await env.completed(await sync(env, engine, mode))
        assert tree(env.local) == tree(env.remote)
        assert (env.local / BAD).read_text() == (env.remote / BAD).read_text() == version
        assert job_warnings(job) == ([warning(SyncWarningCode.NAME_NOT_UTF8, BAD_SHOWN)] if was_local else [])


async def test_per_file_action_on_a_name_that_is_not_utf8_fails_cleanly(env: Env):
    """rclone reports such a name with U+FFFD in place of the bad bytes, so
    the diff's path names no file: the diff warns about it, and the action
    fails for that file, saying why, and changes nothing."""
    write(env.local, BAD, "v1")
    write(env.local, "ok.txt", "ok")
    engine = env.engine()
    diff = await engine.enhanced_diff()
    assert diff.error is None and len(diff.files) == 2
    assert diff.warnings == [warning(SyncWarningCode.NAME_NOT_UTF8, BAD_SHOWN)]

    result = await engine.selective_sync([SelectiveSyncItem(path=f.path, action=FileAction.PUSH) for f in diff.files])

    assert (result.succeeded, result.failed) == (1, 1)
    assert result.errors[0].error == NAME_NOT_UTF8
    assert "not valid UTF-8" in NAME_NOT_UTF8
    assert set(os.listdir(env.remote)) == {"ok.txt"}
    assert (env.local / BAD).read_text() == "v1"


async def test_two_way_never_loses_a_version_of_a_name_that_is_not_utf8(env: Env):
    """bisync (rclone 1.75.1) lists such a name with U+FFFD, so it cannot
    match it with the file on its next runs: an edit is carried over a run
    late, as a conflict whose loser is kept as ``*.remote-conflict1.txt``.
    Every job warns about the name, and every version the user wrote stays
    on both sides or in a conflict copy."""
    write(env.local, BAD, "v1", mtime=time.time() - 3600)
    write(env.local, "ok.txt", "ok")
    engine = env.engine(sync_mode="two_way")
    bad = [warning(SyncWarningCode.NAME_NOT_UTF8, BAD_SHOWN)]
    assert job_warnings(await env.completed(await engine.two_way_sync())) == bad
    for version in ("v2", "v3"):
        write(env.local, BAD, version)
        assert job_warnings(await env.completed(await engine.two_way_sync())) == bad
        assert (env.local / BAD).read_text() == version
    await env.completed(await engine.two_way_sync())

    assert tree(env.local) == tree(env.remote)
    kept = {(env.local / n).read_text() for n in os.listdir(env.local) if n.startswith("bad")}
    kept |= {Path(p).read_text() for p in (env.local / TRASH_DIR).rglob("bad*")}
    assert kept >= {"v1", "v3"}, kept
    assert (env.remote / BAD).read_text() == "v3"


# --- Symbolic links ---


@pytest.mark.parametrize("mode", ["push", "pull", "two_way"])
async def test_symlinks_are_neither_followed_nor_copied(env: Env, mode: str, tmp_path: Path):
    """rclone's default for a local folder: a symbolic link is skipped (with a notice).

    Neither the link nor what it points to reaches the other side, and the
    link and its target are left alone, whatever they point at: a file or a
    folder outside the synced folder, or nothing. With no remote item of
    the same name, that is no warning.
    """
    outside = tmp_path / "outside"
    write(outside, "secret.txt", "outside the profile")
    write(env.local, "a.txt", "a")
    os.symlink(outside / "secret.txt", env.local / "link-to-file.txt")
    os.symlink(outside, env.local / "link-to-folder")
    os.symlink("nowhere", env.local / "dangling")
    write(env.remote, "a.txt", "a")
    engine = engine_for(env, mode)

    await env.completed(await sync(env, engine, mode))
    job = await env.completed(await sync(env, engine, mode))

    assert job_warnings(job) == []
    assert [p for p in env.remote.rglob("*") if p.is_symlink()] == []
    assert "secret.txt" not in {p.name for p in env.remote.rglob("*")}
    for link in ("link-to-file.txt", "link-to-folder", "dangling"):
        assert (env.local / link).is_symlink()
    assert (outside / "secret.txt").read_text() == "outside the profile"


def shadowed(env: Env, outside: Path) -> None:
    """Local links named like a remote file and a remote folder; the targets are outside the profile."""
    write(outside, "t.txt", "target")
    os.symlink(outside / "t.txt", env.local / "clash.txt")
    os.symlink(outside, env.local / "clash-folder")
    write(env.local, "a.txt", "a")
    write(env.remote, "a.txt", "a")
    write(env.remote, "clash.txt", "remote file")
    write(env.remote, "clash-folder/inside.txt", "remote folder")


def links_intact(env: Env, outside: Path) -> None:
    assert (env.local / "clash.txt").is_symlink() and (env.local / "clash-folder").is_symlink()
    # Nothing was written through the link into the folder it points to.
    assert sorted(os.listdir(outside)) == ["t.txt"] and (outside / "t.txt").read_text() == "target"


async def test_push_moves_a_remote_item_shadowed_by_a_local_symlink_to_the_trash_and_says_so(
        env: Env, tmp_path: Path):
    """A push treats a local link as no file: the remote file or folder of that
    name is deleted, recoverably, and the job warns about each."""
    outside = tmp_path / "outside"
    shadowed(env, outside)
    engine = env.engine()
    expected = [warning(SyncWarningCode.SYMLINK_SHADOW, "clash-folder", "clash.txt")]
    assert (await engine.preview_sync()).warnings == expected

    job = await env.completed(await engine.push())

    assert job_warnings(job) == [warning(SyncWarningCode.SYMLINK_TRASHED, "clash-folder", "clash.txt")]
    assert not (env.remote / "clash.txt").exists() and not (env.remote / "clash-folder").exists()
    assert trash(env.remote) == {
        "clash.txt": hashlib.blake2b(b"remote file", digest_size=16).hexdigest(),
        os.path.join("clash-folder", "inside.txt"): hashlib.blake2b(b"remote folder", digest_size=16).hexdigest(),
    }
    links_intact(env, outside)


@pytest.mark.parametrize("mode", ["pull", "two_way"])
async def test_pull_and_two_way_keep_a_local_symlink_and_the_remote_item_it_shadows(
        env: Env, mode: str, tmp_path: Path):
    """rclone alone would copy the remote file over the link (replacing it) and
    write the remote folder's files through the link into the folder it
    points to, outside the profile. OmniSync leaves both paths out of the
    run: the link, its target and the remote items stay as they are, every
    run warns, and once the link is gone the remote items arrive."""
    outside = tmp_path / "outside"
    shadowed(env, outside)
    engine = engine_for(env, mode)
    assert (await engine.enhanced_diff()).warnings == [
        warning(SyncWarningCode.SYMLINK_SHADOW, "clash-folder", "clash.txt")]
    kept = [warning(SyncWarningCode.SYMLINK_KEPT, "clash-folder", "clash.txt")]

    for _ in range(2):
        job = await env.completed(await sync(env, engine, mode))
        assert job_warnings(job) == kept
        links_intact(env, outside)
        assert (env.remote / "clash.txt").read_text() == "remote file"
        assert (env.remote / "clash-folder" / "inside.txt").read_text() == "remote folder"
        assert trash(env.local) == {} and trash(env.remote) == {}

    (env.local / "clash.txt").unlink()
    (env.local / "clash-folder").unlink()
    job = await env.completed(await sync(env, engine, mode))
    assert job_warnings(job) == []
    assert (env.local / "clash.txt").read_text() == "remote file"
    assert (env.local / "clash-folder" / "inside.txt").read_text() == "remote folder"


async def test_two_way_keeps_the_remote_file_when_a_synced_file_becomes_a_symlink(env: Env, tmp_path: Path):
    """A file both sides had, replaced locally by a link: bisync would see it
    deleted locally and delete it remotely. Left out of the run, it is gone
    from both of bisync's listings at once, which bisync takes as deleted on
    both sides: nothing changes, the remote file stays."""
    write(tmp_path / "outside", "t.txt", "target")
    write(env.local, "a.txt", "a")
    write(env.local, "clash.txt", "synced")
    engine = env.engine(sync_mode="two_way")
    await env.completed(await engine.two_way_sync())

    (env.local / "clash.txt").unlink()
    os.symlink(tmp_path / "outside" / "t.txt", env.local / "clash.txt")
    job = await env.completed(await engine.two_way_sync())

    assert job_warnings(job) == [warning(SyncWarningCode.SYMLINK_KEPT, "clash.txt")]
    assert (env.remote / "clash.txt").read_text() == "synced"
    assert (env.local / "clash.txt").is_symlink()
    assert trash(env.remote) == {}


async def test_per_file_pull_never_replaces_a_local_symlink(env: Env, tmp_path: Path):
    write(tmp_path / "outside", "t.txt", "target")
    os.symlink(tmp_path / "outside" / "t.txt", env.local / "clash.txt")
    write(env.remote, "clash.txt", "remote file")
    engine = env.engine()
    diff = await engine.enhanced_diff()
    assert [f.path for f in diff.files] == ["clash.txt"]

    result = await engine.selective_sync([SelectiveSyncItem(path="clash.txt", action=FileAction.PULL)])

    assert (result.failed, result.errors[0].error) == (1, LOCAL_SYMLINK)
    assert (env.local / "clash.txt").is_symlink()
    assert (tmp_path / "outside" / "t.txt").read_text() == "target"


# --- Empty files and folders ---


@pytest.mark.parametrize("mode", MODES)
async def test_empty_files_sync_and_empty_folders_follow_the_mode(env: Env, mode: str):
    """Empty files sync like any other. Empty folders: a two-way sync carries
    them (--create-empty-src-dirs); push, pull and the per-file actions
    copy files only."""
    side = source(env, mode)
    write(side, "empty.txt", b"")
    write(side, "sub/also-empty", b"")
    (side / "empty-folder" / "nested").mkdir(parents=True)
    engine = engine_for(env, mode)

    await env.completed(await sync(env, engine, mode))

    assert tree(env.local) == tree(env.remote) and len(tree(env.local)) == 2
    assert all((root / "empty.txt").stat().st_size == 0 for root in (env.local, env.remote))
    other = env.local if side == env.remote else env.remote
    assert empty_dirs(side) == {os.path.join("empty-folder", "nested")}
    assert empty_dirs(other) == (empty_dirs(side) if mode == "two_way" else set())


# --- Files that change during a sync ---


@pytest.mark.parametrize("mode", ["push", "two_way"])
async def test_files_changing_during_a_sync_lose_nothing(env: Env, mode: str):
    """A file rewritten while rclone copies it: the run completes or fails
    (a two-way run then asks for a resync, "source file is being
    updated"), never crashes, and once the writes stop the last version
    is on both sides."""
    for i in range(200):
        write(env.local, f"f{i:03d}.txt", f"file {i}")
    write(env.local, "big.bin", os.urandom(16 << 20))
    write(env.local, "hot.txt", "version 0")
    engine = engine_for(env, mode)
    stop = threading.Event()
    writes = 0

    def writer() -> None:
        nonlocal writes
        data = bytearray(os.urandom(16 << 20))
        while not stop.is_set():
            writes += 1
            data[:8] = writes.to_bytes(8, "big")
            (env.local / "big.bin").write_bytes(data)
            (env.local / "hot.txt").write_text(f"version {writes}")

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        job = await env.job(await sync(env, engine, mode))
    finally:
        stop.set()
        thread.join()
    assert job.status in ("completed", "failed")

    # Quiet now: the next runs (a resync where the two-way run asked for one) converge.
    for _ in range(2):
        if mode == "two_way" and engine._state.resync_required:
            await env.completed(await engine.resync())
        await env.job(await sync(env, engine, mode))
    assert tree(env.local) == tree(env.remote)
    assert (env.remote / "hot.txt").read_text() == f"version {writes}"
    assert partials(env.local, env.remote) == []


# --- Folder paths at bisync's name limit, in bytes rather than characters ---


async def test_two_way_counts_the_folder_paths_in_bytes(tmp_path: Path, monkeypatch):
    """Multi-byte folder names: bisync's file names (session name + suffix,
    at most 255 bytes) fit in characters but not in bytes, so the run
    must use the short names; one counted in characters would fail."""
    emoji = "📁"
    base = None
    for k in range(1, 120):
        candidate = tmp_path / (emoji * k)
        local, remote = str(candidate / "local"), f"testremote:{candidate / 'remote'}"
        fits_in_chars = len(bisync_session_name(local, remote)) + len(LONGEST_SUFFIX) <= NAME_MAX
        if fits_in_chars and not bisync_names_fit(local, remote):
            base = candidate
            break
    if base is None:
        pytest.skip("TMPDIR is too long to place a path between the two limits")
    env, db = await make_env(base, monkeypatch)
    try:
        assert not bisync_names_fit(str(env.local), f"testremote:{env.remote}")
        assert len(bisync_session_name(str(env.local), f"testremote:{env.remote}")) + len(LONGEST_SUFFIX) <= NAME_MAX
        write(env.local, "a.txt", "a")
        write(env.remote, "b.txt", "b")
        engine = env.engine(sync_mode="two_way")
        await env.completed(await engine.two_way_sync())
        write(env.local, "c.txt", "c")
        job = await env.completed(await engine.two_way_sync())
        assert job.direction == "two_way"
        assert tree(env.local) == tree(env.remote) == {
            n: hashlib.blake2b(n[0].encode(), digest_size=16).hexdigest() for n in ("a.txt", "b.txt", "c.txt")}
        assert all(len(p.encode()) <= NAME_MAX for p in os.listdir(bisync_workdir(1)))
    finally:
        for engine in env.engines:
            await engine.stop()
        await db.dispose()
