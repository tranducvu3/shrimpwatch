"""MongoDB Change Streams on incidents and events (needs a replica set: Atlas, or the Compose single-node set)."""

from __future__ import annotations

from pymongo.change_stream import DatabaseChangeStream

from app.db.mongo import get_db
from app.db.pipelines import incident_changes_pipeline

# try_next() waits at most this long for a change, so the caller can send keep-alives and notice disconnects
MAX_AWAIT_MS = 5000


def watch_incident_changes() -> DatabaseChangeStream:
    """Raises pymongo OperationFailure on a standalone mongod (no change streams there)."""
    return get_db().watch(incident_changes_pipeline(), full_document="updateLookup", max_await_time_ms=MAX_AWAIT_MS)
