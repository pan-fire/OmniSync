"""A profile's sync window: when automatic syncs may run.

The window is ``{"days": [0..6], "start": "HH:MM", "end": "HH:MM"}`` in the
server's local time (the container's TZ). ``days`` are the days a window
starts on (0 = Monday). When ``end`` is before ``start`` the window spans
midnight: 22:00-06:00 on Friday runs until Saturday 06:00. The window is
half-open: open at ``start``, closed again at ``end``.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _parts(window: dict) -> tuple[set[int], int, int] | None:
    try:
        days = {int(d) for d in window.get("days", range(7))}
        return days, _minutes(str(window["start"])), _minutes(str(window["end"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def local_now() -> datetime:
    """Now, in the server's local time zone (aware)."""
    return datetime.now().astimezone()


def window_open(window: dict | None, now: datetime | None = None) -> bool:
    """Whether automatic syncs may run at ``now`` (no window, or an unreadable one: always)."""
    if not window:
        return True
    parts = _parts(window)
    if parts is None:
        return True
    days, start, end = parts
    now = now or local_now()
    minute = now.hour * 60 + now.minute
    today = now.weekday()
    if start < end:
        return today in days and start <= minute < end
    # Spans midnight: the evening part of today's window, or the morning
    # part of yesterday's.
    return (today in days and minute >= start) or ((today - 1) % 7 in days and minute < end)


def next_window_start(window: dict | None, now: datetime | None = None) -> datetime | None:
    """When the window opens next (None while it is open, or without a window)."""
    if not window or window_open(window, now):
        return None
    parts = _parts(window)
    if parts is None:
        return None
    days, start, _end = parts
    now = now or local_now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(8):
        day = midnight + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = day.replace(hour=start // 60, minute=start % 60)
        if candidate > now:
            return candidate
    return None
