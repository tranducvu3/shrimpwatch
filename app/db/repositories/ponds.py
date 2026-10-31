from __future__ import annotations

from app.core.clock import utcnow
from app.db.mongo import ponds_collection


def list_all() -> list[dict]:
    return list(ponds_collection().find({}, {"_id": 1, "name": 1, "pump": 1, "water_node": 1, "scenarios": 1}))


def set_pump_failed(pond_id: str, failed: bool) -> bool:
    """Tra ve False neu khong co ao nay."""
    res = ponds_collection().update_one(
        {"_id": pond_id},
        {"$set": {"pump.failed": failed, "pump.failed_since": utcnow() if failed else None}},
    )
    return res.matched_count > 0


def set_scenario(pond_id: str, scenario: str, doc: dict) -> bool:
    """Ghi de kich ban cung loai (moi loai toi da 1 kich ban moi ao). False neu khong co ao."""
    res = ponds_collection().update_one({"_id": pond_id}, {"$set": {f"scenarios.{scenario}": doc}})
    return res.matched_count > 0


def clear_scenarios(pond_id: str, scenario: str | None = None) -> bool:
    """Xoa 1 loai kich ban, hoac tat ca neu scenario=None. False neu khong co ao."""
    field = f"scenarios.{scenario}" if scenario else "scenarios"
    res = ponds_collection().update_one({"_id": pond_id}, {"$unset": {field: ""}})
    return res.matched_count > 0
