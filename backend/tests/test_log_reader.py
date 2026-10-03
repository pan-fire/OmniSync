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
