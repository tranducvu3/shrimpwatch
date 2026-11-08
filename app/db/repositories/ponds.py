from __future__ import annotations

from app.core.clock import utcnow
from app.db.mongo import ponds_collection


def list_all() -> list[dict]:
    return list(
        ponds_collection().find({}, {"_id": 1, "name": 1, "pump": 1, "water_node": 1, "scenarios": 1, "conditions": 1})
    )


def set_pump_failed(pond_id: str, failed: bool) -> bool:
    """Returns False if the pond does not exist."""
    res = ponds_collection().update_one(
        {"_id": pond_id},
        {"$set": {"pump.failed": failed, "pump.failed_since": utcnow() if failed else None}},
    )
    return res.matched_count > 0


def set_scenario(pond_id: str, scenario: str, doc: dict) -> bool:
    """Overwrite the scenario of the same type (at most 1 per type per pond). False if the pond does not exist."""
    res = ponds_collection().update_one({"_id": pond_id}, {"$set": {f"scenarios.{scenario}": doc}})
    return res.matched_count > 0


def clear_scenarios(pond_id: str, scenario: str | None = None) -> bool:
    """Remove one scenario type, or all of them if scenario=None. False if the pond does not exist."""
    field = f"scenarios.{scenario}" if scenario else "scenarios"
    res = ponds_collection().update_one({"_id": pond_id}, {"$unset": {field: ""}})
    return res.matched_count > 0


def set_condition(pond_id: str, condition: str, doc: dict) -> bool:
    """Start (or restart) a condition on a pond. False if the pond does not exist."""
    res = ponds_collection().update_one({"_id": pond_id}, {"$set": {f"conditions.{condition}": doc}})
    return res.matched_count > 0


def clear_conditions(pond_id: str, condition: str | None = None) -> bool:
    """Remove one condition, or all of them if condition=None. False if the pond does not exist."""
    field = f"conditions.{condition}" if condition else "conditions"
    res = ponds_collection().update_one({"_id": pond_id}, {"$unset": {field: ""}})
    return res.matched_count > 0
