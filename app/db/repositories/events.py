"""Nhat ky su kien (`events`): moi thay doi quat/su co deu ghi 1 dong."""

from __future__ import annotations

from app.core.clock import utcnow
from app.db.mongo import events_collection


def record(event: str, pond_id: str | None, incident_id: str | None = None, details: dict | None = None) -> None:
    doc: dict = {"timestamp": utcnow(), "pond_id": pond_id, "event": event}
    if incident_id is not None:
        doc["incident_id"] = incident_id
    if details is not None:
        doc["details"] = details
    events_collection().insert_one(doc)
