from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import utcnow
from app.db.mongo import incidents_collection, session
from app.db.pipelines import incident_timeline_pipeline
from app.domain.incident_lifecycle import ACTIVE_STATES
from app.domain.models import IncidentState


def get(incident_id: str) -> dict | None:
    return incidents_collection().find_one({"_id": incident_id}, session=session())


def find_active(pond_id: str) -> dict | None:
    return incidents_collection().find_one(
        {"pond_id": pond_id, "state": {"$in": [s.value for s in ACTIVE_STATES]}}, session=session()
    )


_PANEL_FIELDS = {"pond_id": 1, "state": 1, "severity": 1, "title": 1, "created_at": 1, "recovered_at": 1}


def list_active() -> list[dict]:
    """Unrecovered incidents (at most 1 per pond, so few rows), with the fields the incident lists show."""
    return list(incidents_collection().find({"state": {"$in": [s.value for s in ACTIVE_STATES]}}, _PANEL_FIELDS))


def active_pond_ids() -> set[str]:
    return {doc["pond_id"] for doc in list_active()}


def recovered_since(since: datetime, limit: int = 100) -> list[dict]:
    """Incidents recovered since `since`, newest first (index state + recovered_at)."""
    query = {"state": IncidentState.RECOVERED.value, "recovered_at": {"$gte": since}}
    return list(incidents_collection().find(query, _PANEL_FIELDS).sort("recovered_at", -1).limit(limit))


def list_recent(state: IncidentState | None = None, limit: int = 50) -> list[dict]:
    query = {"state": state.value} if state else {}
    return list(incidents_collection().find(query).sort("created_at", -1).limit(limit))


def insert(doc: dict[str, Any]) -> None:
    """Raises DuplicateKeyError if the pond already has an active incident (unique index one_active_incident_per_pond).

    Raised, not caught: inside a transaction the error has already aborted it, so nothing else may be written.
    """
    incidents_collection().insert_one(doc, session=session())


def update_fields(incident_id: str, fields: dict[str, Any]) -> None:
    incidents_collection().update_one(
        {"_id": incident_id}, {"$set": {"updated_at": utcnow(), **fields}}, session=session()
    )


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
    incidents_collection().update_one({"_id": incident_id}, update, session=session())


def add_remedy(incident_id: str, remedy: str, check: dict[str, Any]) -> None:
    """Record a remedy as applied (once) and restart the check of whether it works."""
    incidents_collection().update_one(
        {"_id": incident_id},
        {"$addToSet": {"remedies": remedy}, "$set": {"updated_at": utcnow(), "remedy_check": check}},
        session=session(),
    )


def set_remedy_outcome(incident_id: str, applied_at: datetime, fields: dict[str, Any]) -> bool:
    """Write the check's result; False when the operator applied another remedy or reinvestigated meanwhile."""
    result = incidents_collection().update_one(
        {"_id": incident_id, "state": IncidentState.RESOLVING.value, "remedy_check.applied_at": applied_at},
        {"$set": {"updated_at": utcnow(), **{f"remedy_check.{k}": v for k, v in fields.items()}}},
        session=session(),
    )
    return result.modified_count == 1


def timeline(incident_id: str) -> dict | None:
    rows = list(incidents_collection().aggregate(incident_timeline_pipeline(incident_id)))
    return rows[0] if rows else None
