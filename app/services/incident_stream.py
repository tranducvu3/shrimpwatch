"""Live incident updates for the control panel: MongoDB Change Streams turned into server-sent events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.log import get_logger
from app.db.repositories import changes as changes_repo

logger = get_logger("services.incident_stream")


class StreamUnavailable(RuntimeError):
    """MongoDB cannot open a change stream (standalone mongod): the panel keeps polling."""


def open_stream() -> Any:
    try:
        return changes_repo.watch_incident_changes()
    except Exception as e:
        logger.warning("change stream unavailable", extra={"err": str(e)})
        raise StreamUnavailable(str(e)) from e


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
    """One `data:` line per change, a keep-alive comment when nothing changed for MAX_AWAIT_MS. Closes the stream."""
    try:
        yield "retry: 5000\n\n"
        while not await is_disconnected():
            change = await run_in_threadpool(stream.try_next)
            if change is None:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(change_message(change), default=str)}\n\n"
    finally:
        await run_in_threadpool(stream.close)
