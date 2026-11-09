from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.db.mongo import incidents_collection
from app.db.pipelines import incident_timeline_pipeline
from app.domain.incident_lifecycle import ACTIVE_STATES
from app.domain.models import IncidentState


def get(incident_id: str) -> dict | None:
    return incidents_collection().find_one({"_id": incident_id})


def find_active(pond_id: str) -> dict | None:
    return incidents_collection().find_one({"pond_id": pond_id, "state": {"$in": [s.value for s in ACTIVE_STATES]}})


def active_pond_ids() -> set[str]:
    """Ponds with an unrecovered incident (at most 1 incident per pond, so few rows)."""
    cursor = incidents_collection().find({"state": {"$in": [s.value for s in ACTIVE_STATES]}}, {"pond_id": 1})
    return {doc["pond_id"] for doc in cursor}


def list_recent(state: IncidentState | None = None, limit: int = 50) -> list[dict]:
    query = {"state": state.value} if state else {}
    return list(incidents_collection().find(query).sort("created_at", -1).limit(limit))


def insert(doc: dict[str, Any]) -> bool:
    """False if the pond already has an active incident (unique index one_active_incident_per_pond)."""
    try:
        incidents_collection().insert_one(doc)
    except DuplicateKeyError:
        return False
    return True


def update_fields(incident_id: str, fields: dict[str, Any]) -> None:
    incidents_collection().update_one({"_id": incident_id}, {"$set": {"updated_at": utcnow(), **fields}})


def last_closed_at(pond_id: str) -> datetime | None:
    """When the pond's latest recovered incident was closed (updated_at covers incidents older than recovered_at)."""
    doc = incidents_collection().find_one(
        {"pond_id": pond_id, "state": IncidentState.RECOVERED.value}, {"updated_at": 1}, sort=[("updated_at", -1)]
    )
    return doc["updated_at"] if doc else None


def set_state(
    incident_id: str, state: IncidentState, action: str | None = None, fields: dict[str, Any] | None = None
) -> None:
    now = utcnow()
    update: dict[str, Any] = {"$set": {"updated_at": now, "state": state.value, **(fields or {})}}
    if action:
        update["$push"] = {"actions": {"time": now, "state": state.value, "action": action}}
    incidents_collection().update_one({"_id": incident_id}, update)


def timeline(incident_id: str) -> dict | None:
    rows = list(incidents_collection().aggregate(incident_timeline_pipeline(incident_id)))
    return rows[0] if rows else None
