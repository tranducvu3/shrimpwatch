"""System faults switched on from the control panel (`system_faults`): one document per fault type, _id = the type."""

from __future__ import annotations

from datetime import datetime

from app.db.mongo import system_faults_collection


def list_all() -> list[dict]:
    return list(system_faults_collection().find({}))


def set_fault(fault: str, started_at: datetime, until: datetime) -> None:
    """Switch a fault on (again) until `until`."""
    system_faults_collection().update_one(
        {"_id": fault}, {"$set": {"started_at": started_at, "until": until}}, upsert=True
    )


def clear(fault: str) -> bool:
    """Switch a fault off. False if it was not stored."""
    return system_faults_collection().delete_one({"_id": fault}).deleted_count == 1
