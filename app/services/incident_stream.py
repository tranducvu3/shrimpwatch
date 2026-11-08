"""Live incident updates for the control panel: MongoDB Change Streams turned into server-sent events."""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from app.core.log import get_logger
from app.db.repositories import changes as changes_repo

logger = get_logger("services.incident_stream")


# Each open stream holds a worker thread (waiting in try_next) and a MongoDB connection: cap them, the panel polls anyway
MAX_STREAMS = 10
_open_streams = 0
_lock = threading.Lock()


class StreamUnavailable(RuntimeError):
    """No live stream (standalone mongod, or too many open): the panel keeps polling."""


def open_stream() -> Any:
    global _open_streams
    with _lock:
        if _open_streams >= MAX_STREAMS:
            raise StreamUnavailable(f"{MAX_STREAMS} live connections already open")
        _open_streams += 1
    try:
        return changes_repo.watch_incident_changes()
    except Exception as e:
        _release()
        logger.warning("change stream unavailable", extra={"err": str(e)})
        raise StreamUnavailable(str(e)) from e


def _release() -> None:
    global _open_streams
    with _lock:
        _open_streams = max(0, _open_streams - 1)


def change_message(change: dict) -> dict:
    """The few fields the panel needs to know what to refresh."""
    doc = change.get("fullDocument") or {}
    collection = change["ns"]["coll"]
    incident_id = change["documentKey"]["_id"] if collection == "incidents" else doc.get("incident_id")
    message = {
        "collection": collection,
        "op": change["operationType"],
        "incident_id": incident_id,
        "pond_id": doc.get("pond_id"),
    }
    for key in ("state", "severity", "event"):
        if doc.get(key) is not None:
            message[key] = doc[key]
    return message


async def sse_events(stream: Any, is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[str]:
    """One `data:` line per change, a keep-alive comment when nothing changed for MAX_AWAIT_MS.

    Takes over a stream from open_stream: closes it and frees its slot when the client goes away.
    """
    try:
        yield "retry: 5000\n\n"
        while not await is_disconnected():
            try:
                change = await run_in_threadpool(stream.try_next)
            except PyMongoError as e:
                # Network error or invalidated stream: end it cleanly, the browser reconnects after `retry`
                logger.warning("change stream ended", extra={"err": str(e)})
                break
            if change is None:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(change_message(change), default=str)}\n\n"
    finally:
        _release()
        await run_in_threadpool(stream.close)
