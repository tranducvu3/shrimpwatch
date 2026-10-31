from __future__ import annotations

from typing import Any

from app.core.clock import utcnow
from app.db.mongo import incidents_collection
from app.db.pipelines import incident_timeline_pipeline
from app.domain.incident_lifecycle import ACTIVE_STATES
from app.domain.models import IncidentState


def get(incident_id: str) -> dict | None:
    return incidents_collection().find_one({"_id": incident_id})


def find_active(pond_id: str) -> dict | None:
    return incidents_collection().find_one({"pond_id": pond_id, "state": {"$in": [s.value for s in ACTIVE_STATES]}})


def list_recent(state: IncidentState | None = None, limit: int = 50) -> list[dict]:
    query = {"state": state.value} if state else {}
    return list(incidents_collection().find(query).sort("created_at", -1).limit(limit))


def insert(doc: dict[str, Any]) -> None:
    incidents_collection().insert_one(doc)


def update_fields(incident_id: str, fields: dict[str, Any]) -> None:
    incidents_collection().update_one({"_id": incident_id}, {"$set": {"updated_at": utcnow(), **fields}})


def set_state(incident_id: str, state: IncidentState, action: str | None = None) -> None:
    now = utcnow()
    update: dict[str, Any] = {"$set": {"updated_at": now, "state": state.value}}
    if action:
        update["$push"] = {"actions": {"time": now, "state": state.value, "action": action}}
    incidents_collection().update_one({"_id": incident_id}, update)


def timeline(incident_id: str) -> dict | None:
    rows = list(incidents_collection().aggregate(incident_timeline_pipeline(incident_id)))
    return rows[0] if rows else None
