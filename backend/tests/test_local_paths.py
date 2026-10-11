"""Paths in a local folder that never lead through a symbolic link (local_paths.py), on real files.

A link anywhere on the way (a parent folder, or the file itself) would
let a pull, a restore or a conflict move write outside the synced folder.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from backend.services import local_paths
from backend.services.local_paths import (
    LinkedPathError,
    check_no_link,
    link_on_path,
    links_on_paths,
    move_within,
)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """(root, outside): root/dir/file, root/ld -> outside, root/lf -> outside/f, root/dangling."""
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "dir").mkdir(parents=True)
    (root / "dir" / "file").write_text("in")
    outside.mkdir()
    (outside / "f").write_text("outside")
    (root / "ld").symlink_to(outside)
    (root / "lf").symlink_to(outside / "f")
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    return root, outside


@pytest.mark.parametrize(("rel", "link"), [
    ("dir/file", None),
    ("dir/new/deeper.txt", None),       # missing parts: nothing below them can be a link
    ("missing/x", None),
    ("ld/f", "ld"),                     # a parent folder
    ("ld/new/x", "ld"),
    ("ld", "ld"),                       # the file itself: a link to a folder,
    ("lf", "lf"),                       # to a file,
    ("dangling", "dangling"),           # or to nothing
    ("dangling/x", "dangling"),
    ("dir/file/x", None),               # a file on the way: the write fails on its own
    ("../outside/f", "../outside/f"),   # no plain path: returned whole
    ("", ""),
    ("a/./b", "a/./b"),
])
def test_link_on_path(tree, rel: str, link: str | None):
    root, _ = tree
    assert link_on_path(str(root), rel) == link
    assert links_on_paths(str(root), [rel]) == ([] if link is None else [link])


def test_the_folder_itself_may_be_a_link(tree, tmp_path: Path):
    """Where the user put the folder is theirs to choose: only what is below it is checked."""
    root, _ = tree
    (tmp_path / "alias").symlink_to(root)
    assert link_on_path(str(tmp_path / "alias"), "dir/file") is None
    assert link_on_path(str(tmp_path / "alias"), "ld/f") == "ld"


def test_many_paths_share_their_checks(tree):
    root, _ = tree
    rels = [f"dir/{i}" for i in range(50)] + ["ld/a", "ld/b", "lf", "dir/file"]
    assert links_on_paths(str(root), rels) == ["ld", "lf"]


def test_a_folder_that_resolves_outside_is_refused(tree, monkeypatch):
    """Defence in depth: a folder that resolves outside although no part was a link (swapped meanwhile)."""
    root, outside = tree
    real = os.path.realpath
    monkeypatch.setattr(local_paths.os.path, "realpath",
                        lambda p: str(outside) if str(p).endswith("dir") else real(p))
    assert link_on_path(str(root), "dir/x") == "dir/x"
    assert links_on_paths(str(root), ["dir/x"]) == ["dir/x"]


def test_check_no_link(tree):
    root, _ = tree
    assert check_no_link(str(root), "dir/file") == str(root / "dir" / "file")
    with pytest.raises(LinkedPathError, match="'ld' in the local folder is a symbolic link") as caught:
        check_no_link(str(root), "ld/f")
    assert (caught.value.rel, caught.value.link) == ("ld/f", "ld")


def test_move_within_moves_and_makes_folders(tree):
    root, _ = tree
    (root / "dir" / "old").write_text("old")
    move_within(str(root), "dir/file", "new/deep/file")
    assert (root / "new" / "deep" / "file").read_text() == "in" and not (root / "dir" / "file").exists()
    move_within(str(root), "new/deep/file", "dir/old")  # replaces what is there
    assert (root / "dir" / "old").read_text() == "in"


def test_move_within_moves_a_link_itself(tree):
    root, outside = tree
    move_within(str(root), "lf", "dir/lf")
    assert (root / "dir" / "lf").is_symlink() and (outside / "f").read_text() == "outside"


def test_a_link_swapped_in_after_the_check_fails_the_move(tree):
    """The race a path check cannot close: the folder is checked, then made a
    link. The move opens each folder with O_NOFOLLOW and fails instead of
    writing into the folder the link points to."""
    root, outside = tree
    (root / "target").mkdir()
    check_no_link(str(root), "target/file")      # the check passes ...
    (root / "target").rmdir()
    (root / "target").symlink_to(outside)        # ... then the folder is swapped

    with pytest.raises(LinkedPathError) as caught:
        move_within(str(root), "dir/file", "target/f")
    assert caught.value.link == "target"
    assert (outside / "f").read_text() == "outside" and (root / "dir" / "file").read_text() == "in"

    with pytest.raises(LinkedPathError):  # nor read through one
        move_within(str(root), "ld/f", "dir/f")
    assert (outside / "f").read_text() == "outside"


def test_move_within_refuses_what_is_no_plain_path(tree):
    root, _ = tree
    for src, dst in (("../outside/f", "dir/x"), ("dir/file", "")):
        with pytest.raises(LinkedPathError):
            move_within(str(root), src, dst)


def test_a_file_on_the_way_is_an_os_error(tree):
    root, _ = tree
    with pytest.raises(NotADirectoryError):
        move_within(str(root), "dir/file", "dir/file/x")
    with pytest.raises(FileNotFoundError):
        move_within(str(root), "missing/x", "dir/x")


def test_a_move_onto_another_file_system_copies_after_checking(tree, monkeypatch):
    """A mount inside the folder: the rename fails with EXDEV, and the move is
    made by path instead, each folder on the way checked right before."""
    root, outside = tree
    replace = os.replace

    def cross_device(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is not None:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return replace(src, dst)

    monkeypatch.setattr(local_paths.os, "replace", cross_device)
    move_within(str(root), "dir/file", "other/file")
    assert (root / "other" / "file").read_text() == "in"

    (root / "dir" / "second").write_text("2")

    def swap_then_cross_device(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        (root / "other").rename(root / "other.real")
        (root / "other").symlink_to(outside)
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(local_paths.os, "replace", swap_then_cross_device)
    with pytest.raises(LinkedPathError):
        move_within(str(root), "dir/second", "other/g")
    assert sorted(os.listdir(outside)) == ["f"] and (root / "dir" / "second").read_text() == "2"


def test_other_errors_while_opening_a_folder_are_raised(tree, monkeypatch):
    root, _ = tree
    real_open = os.open

    def denied(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None:
            raise PermissionError(errno.EACCES, "denied")
        return real_open(path, flags, mode)

    monkeypatch.setattr(local_paths.os, "open", denied)
    with pytest.raises(PermissionError):
        move_within(str(root), "dir/file", "dir/x")


def test_a_folder_made_meanwhile_is_used(tree, monkeypatch):
    """Two moves making the same folder at once: the one that loses the mkdir opens the other's."""
    root, _ = tree
    real_mkdir = os.mkdir

    def raced(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        raise FileExistsError(errno.EEXIST, "exists")

    monkeypatch.setattr(local_paths.os, "mkdir", raced)
    move_within(str(root), "dir/file", "made/file")
    assert (root / "made" / "file").read_text() == "in"
