"""Log viewer endpoints for OmniSync."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Query

from backend.api.schemas import LogEntryResponse
from backend.services.log_reader import LogReader

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level reference to the log reader, set by main.py at startup
_log_reader: LogReader | None = None


def set_log_reader(reader: LogReader | None) -> None:
    """Set the log reader reference."""
    global _log_reader
    _log_reader = reader


@router.get("/logs", response_model=list[LogEntryResponse])
async def get_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Query(None),
) -> list[LogEntryResponse]:
    """Return paginated log entries, most recent first, optionally of one level."""
    if _log_reader is None:
        return []
    # file I/O off the event loop
    return await asyncio.to_thread(_log_reader.read, skip=skip, limit=limit, level=level)
