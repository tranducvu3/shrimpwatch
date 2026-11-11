"""Event log (`events`): every pump/incident change writes 1 row."""

from __future__ import annotations

from datetime import datetime

from app.core.clock import utcnow
from app.db.mongo import events_collection, session


def record(event: str, pond_id: str | None, incident_id: str | None = None, details: dict | None = None) -> None:
    doc: dict = {"timestamp": utcnow(), "pond_id": pond_id, "event": event}
    if incident_id is not None:
        doc["incident_id"] = incident_id
    if details is not None:
        doc["details"] = details
    events_collection().insert_one(doc, session=session())


# Events that start a fault in the (simulated) pond
FAULT_EVENTS = ("pump_state_change", "condition_start", "scenario_start")


def latest_fault_start(pond_id: str, since: datetime, until: datetime) -> datetime | None:
    """Time of the latest fault injected in the pond within [since, until]; a repaired pump does not count."""
    doc = events_collection().find_one(
        {
            "pond_id": pond_id,
            "timestamp": {"$gte": since, "$lte": until},
            "event": {"$in": list(FAULT_EVENTS)},
            "details.failed": {"$ne": False},
        },
        {"timestamp": 1},
        sort=[("timestamp", -1)],
    )
    return doc["timestamp"] if doc else None
