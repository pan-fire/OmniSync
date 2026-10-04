"""LogReader reads only the tail of the log."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import log_reader as log_reader_module
from backend.services.log_reader import LogReader


def line(i: int, level: str = "INFO") -> str:
    return f"2026-09-27 10:{i // 60 % 60:02}:{i % 60:02},{i % 1000:03} - {level} - backend.x - message {i}\n"


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    path = tmp_path / "omnisync.log"
    with path.open("w") as fh:
        for i in range(5000):
            fh.write(line(i))
            if i % 7 == 0:
                fh.write("  continuation line of a traceback\n")
    return path


def test_most_recent_first_with_paging(log_file: Path) -> None:
    reader = LogReader(log_file)
    page = reader.read(skip=0, limit=3)
    assert [e.message for e in page] == ["message 4999", "message 4998", "message 4997"]
    page2 = reader.read(skip=3, limit=2)
    assert [e.message for e in page2] == ["message 4996", "message 4995"]


def test_reads_only_the_tail(log_file: Path, monkeypatch) -> None:
    read_bytes = 0
    real_open = open

    class CountingFile:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            nonlocal read_bytes
            data = self._fh.read(n)
            read_bytes += len(data)
            return data

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()

    monkeypatch.setattr(log_reader_module, "open", lambda *a, **k: CountingFile(real_open(*a, **k)), raising=False)
    monkeypatch.setattr(log_reader_module, "TAIL_CHUNK", 4096)

    entries = LogReader(log_file).read(skip=0, limit=50)

    assert len(entries) == 50
    assert read_bytes < log_file.stat().st_size / 10


def test_lines_spanning_chunks_and_a_missing_final_newline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(log_reader_module, "TAIL_CHUNK", 7)  # every line spans chunks
    path = tmp_path / "o.log"
    path.write_text(line(1) + line(2, "ERROR") + line(3).rstrip("\n"))

    entries = LogReader(path).read(limit=10)

    assert [(e.level, e.message) for e in entries] == [
        ("INFO", "message 3"), ("ERROR", "message 2"), ("INFO", "message 1"),
    ]


def test_missing_file(tmp_path: Path) -> None:
    assert LogReader(tmp_path / "none.log").read() == []


def test_level_filter_pages_through_that_level_only(tmp_path: Path) -> None:
    path = tmp_path / "omnisync.log"
    with path.open("w") as fh:
        for i in range(1000):
            fh.write(line(i, "ERROR" if i % 100 == 0 else "INFO"))
    reader = LogReader(path)
    # The errors are far apart: none is among the newest 50 entries.
    assert [e.level for e in reader.read(limit=50)] == ["INFO"] * 50
    errors = reader.read(limit=3, level="ERROR")
    assert [e.message for e in errors] == ["message 900", "message 800", "message 700"]
    assert [e.message for e in reader.read(skip=8, limit=5, level="ERROR")] == ["message 100", "message 0"]


async def test_logs_route_filters_by_level(test_client, tmp_path: Path, monkeypatch) -> None:
    from backend.api.routes import logs

    path = tmp_path / "omnisync.log"
    path.write_text(line(1, "ERROR") + line(2, "INFO") + line(3, "WARNING"))
    monkeypatch.setattr(logs, "_log_reader", LogReader(path))
    res = await test_client.get("/logs", params={"level": "ERROR"})
    assert res.status_code == 200
    assert [e["message"] for e in res.json()] == ["message 1"]
    assert (await test_client.get("/logs", params={"level": "LOUD"})).status_code == 422


# --- tracebacks, JSON lines, categories -------------------------------------------

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/backend/x.py", line 3, in run\n'
    "    boom()\n"
    "ValueError: broken\n"
)


def test_traceback_lines_belong_to_the_entry_above(tmp_path: Path) -> None:
    path = tmp_path / "o.log"
    path.write_text(
        line(1)
        + "2026-09-27 10:00:02,000 - ERROR - backend.engine - [req:abcdef123456] Sync crashed\n"
        + TRACEBACK
        + line(3)
    )
    newest, crashed, oldest = LogReader(path).read()
    assert newest.message == "message 3" and newest.exc is None
    assert crashed.message == "Sync crashed"
    assert crashed.logger == "backend.engine"
    assert crashed.request_id == "abcdef123456"
    assert crashed.exc == TRACEBACK.rstrip("\n")
    assert oldest.message == "message 1" and oldest.logger == "backend.x"


def test_traceback_spanning_chunks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(log_reader_module, "TAIL_CHUNK", 11)
    path = tmp_path / "o.log"
    path.write_text("2026-09-27 10:00:02,000 - ERROR - backend.engine - Sync crashed\n" + TRACEBACK)
    [entry] = LogReader(path).read()
    assert entry.exc == TRACEBACK.rstrip("\n")


def test_very_long_continuations_are_cut(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(log_reader_module, "MAX_CONTINUATION_LINES", 3)
    path = tmp_path / "o.log"
    path.write_text(line(1) + "".join(f"  frame {i}\n" for i in range(10)))
    [entry] = LogReader(path).read()
    assert entry.exc == "... (7 more lines)\n  frame 7\n  frame 8\n  frame 9"


def test_lines_before_the_first_entry_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "o.log"
    path.write_text("  orphaned frame\n" + line(1))
    [entry] = LogReader(path).read()
    assert entry.message == "message 1" and entry.exc is None


def _json_line(msg: str, level: str = "INFO", logger: str = "backend.x", **extra: object) -> str:
    import json

    return json.dumps({"ts": "2026-10-04T09:00:00.123Z", "level": level, "logger": logger, "msg": msg, **extra}) + "\n"


def test_json_lines_and_a_switch_between_formats(tmp_path: Path) -> None:
    path = tmp_path / "o.log"
    path.write_text(
        line(1)  # written before OMNISYNC_LOG_FORMAT=json was set
        + _json_line("json one", request_id="r1234567890", exc="Traceback ...\nValueError: x")
        + "{not json}\n"
        + _json_line("json two", level="ERROR", fields={"action": "x"})
    )
    two, one, text = LogReader(path).read()
    assert (two.level, two.message, two.exc) == ("ERROR", "json two", None)
    assert one.request_id == "r1234567890" and one.exc == "Traceback ...\nValueError: x\n{not json}"
    assert one.timestamp.tzinfo is not None and one.timestamp.hour == 9
    assert text.message == "message 1"


def test_category_filters(tmp_path: Path) -> None:
    path = tmp_path / "o.log"
    path.write_text(
        _json_line("sync.start profile=a outcome=ok", logger="backend.audit")
        + line(2, "ERROR")
        + "2026-09-27 10:00:03,000 - WARNING - backend.audit - profile.delete profile=b outcome=refused\n"
        + line(4, "CRITICAL")
        + line(5)
    )
    reader = LogReader(path)
    assert [e.message for e in reader.read(category="audit")] == [
        "profile.delete profile=b outcome=refused", "sync.start profile=a outcome=ok",
    ]
    assert [e.message for e in reader.read(category="errors")] == ["message 4", "message 2"]
    assert [e.message for e in reader.read(category="audit", level="WARNING")] == [
        "profile.delete profile=b outcome=refused",
    ]


async def test_logs_route_filters_by_category(test_client, tmp_path: Path, monkeypatch) -> None:
    from backend.api.routes import logs

    path = tmp_path / "omnisync.log"
    path.write_text(line(1, "ERROR") + "2026-09-27 10:00:02,000 - INFO - backend.audit - [req:abc12345] x.y outcome=ok\n")
    monkeypatch.setattr(logs, "_log_reader", LogReader(path))
    res = await test_client.get("/logs", params={"category": "audit"})
    assert res.status_code == 200
    assert res.json() == [{
        "timestamp": "2026-09-27T10:00:02", "level": "INFO", "message": "x.y outcome=ok",
        "logger": "backend.audit", "request_id": "abc12345", "exc": None,
    }]
    assert [e["message"] for e in (await test_client.get("/logs", params={"category": "errors"})).json()] == [
        "message 1",
    ]
    assert (await test_client.get("/logs", params={"category": "secrets"})).status_code == 422


# --- rotated files (omnisync.log.1, .2, ...) ----------------------------------------


def _rotated(tmp_path: Path, per_file: int = 100, files: int = 4) -> Path:
    """omnisync.log and .1 .. .{files-1}: entry 0 is the oldest (in the last file)."""
    path = tmp_path / "omnisync.log"
    i = 0
    for n in reversed(range(files)):
        target = path if n == 0 else tmp_path / f"omnisync.log.{n}"
        with target.open("w") as fh:
            for _ in range(per_file):
                fh.write(line(i, "ERROR" if i % 50 == 0 else "INFO"))
                if i % 9 == 0:
                    fh.write("  continuation\n")
                i += 1
    return path


def test_paging_continues_into_the_rotated_files_in_order(tmp_path: Path) -> None:
    path = _rotated(tmp_path)
    reader = LogReader(path)
    assert reader.files() == [path, *(tmp_path / f"omnisync.log.{n}" for n in (1, 2, 3))]
    seen: list[str] = []
    for page in range(9):
        seen += [e.message for e in reader.read(skip=page * 50, limit=50)]
    assert seen == [f"message {i}" for i in reversed(range(400))]
    # A page across the boundary between omnisync.log and omnisync.log.1.
    assert [e.message for e in reader.read(skip=98, limit=4)] == [
        "message 301", "message 300", "message 299", "message 298",
    ]


def test_filters_work_across_the_rotated_files(tmp_path: Path) -> None:
    path = _rotated(tmp_path)
    (tmp_path / "omnisync.log.2").write_text(
        (tmp_path / "omnisync.log.2").read_text()
        + "2026-09-27 11:00:00,000 - INFO - backend.audit - [req:abc12345] profile.delete outcome=ok\n"
    )
    reader = LogReader(path)
    errors = reader.read(limit=50, category="errors")
    assert [e.message for e in errors] == [f"message {i}" for i in (350, 300, 250, 200, 150, 100, 50, 0)]
    assert [e.message for e in reader.read(skip=5, limit=2, level="ERROR")] == ["message 100", "message 50"]
    assert [e.message for e in reader.read(category="audit")] == ["profile.delete outcome=ok"]


def test_a_gap_ends_the_rotated_files_and_only_numbered_names_count(tmp_path: Path) -> None:
    path = _rotated(tmp_path, per_file=3)
    (tmp_path / "omnisync.log.2").unlink()  # .3 is not reached past the gap
    (tmp_path / "omnisync.log.old").write_text(line(999))
    reader = LogReader(path)
    assert reader.files() == [path, tmp_path / "omnisync.log.1"]
    assert [e.message for e in reader.read(limit=50)] == [f"message {i}" for i in (11, 10, 9, 8, 7, 6)]


def test_rotated_files_without_the_current_one(tmp_path: Path) -> None:
    path = _rotated(tmp_path, per_file=2, files=2)
    path.unlink()
    assert [e.message for e in LogReader(path).read()] == ["message 1", "message 0"]


def test_reads_no_more_of_the_rotated_files_than_the_page_needs(tmp_path: Path, monkeypatch) -> None:
    path = _rotated(tmp_path, per_file=2000, files=3)
    read_bytes = 0
    real_open = open

    class CountingFile:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            nonlocal read_bytes
            data = self._fh.read(n)
            read_bytes += len(data)
            return data

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()

    monkeypatch.setattr(log_reader_module, "open", lambda *a, **k: CountingFile(real_open(*a, **k)), raising=False)
    monkeypatch.setattr(log_reader_module, "TAIL_CHUNK", 4096)
    # Entries 3990..4009: the end of omnisync.log.1 and the start of omnisync.log.
    entries = LogReader(path).read(skip=1990, limit=20)
    assert [e.message for e in entries] == [f"message {i}" for i in reversed(range(3990, 4010))]
    file_size = path.stat().st_size
    assert read_bytes < file_size + file_size / 10  # all of omnisync.log, a bit of .1, none of .2


def test_a_rotation_while_reading_neither_repeats_nor_skips(tmp_path: Path, monkeypatch) -> None:
    path = _rotated(tmp_path, per_file=5, files=2)
    reader = LogReader(path)
    real_entries = LogReader._entries_from_end
    rotated = False

    def rotate_then_read(self, fh):
        nonlocal rotated
        if not rotated:  # the handler rotates after the files were opened
            rotated = True
            (tmp_path / "omnisync.log.1").rename(tmp_path / "omnisync.log.2")
            path.rename(tmp_path / "omnisync.log.1")
            path.write_text(line(100))
        return real_entries(self, fh)

    monkeypatch.setattr(LogReader, "_entries_from_end", rotate_then_read)
    assert [e.message for e in reader.read(limit=50)] == [f"message {i}" for i in reversed(range(10))]


async def test_logs_route_pages_into_the_rotated_files(test_client, tmp_path: Path, monkeypatch) -> None:
    from backend.api.routes import logs

    path = _rotated(tmp_path, per_file=3, files=3)
    monkeypatch.setattr(logs, "_log_reader", LogReader(path))
    res = await test_client.get("/logs", params={"skip": 4, "limit": 3})
    assert [e["message"] for e in res.json()] == ["message 4", "message 3", "message 2"]
    res = await test_client.get("/logs", params={"level": "ERROR"})
    assert [e["message"] for e in res.json()] == ["message 0"]
