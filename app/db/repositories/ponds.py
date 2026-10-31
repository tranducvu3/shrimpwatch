from __future__ import annotations

from app.core.clock import utcnow
from app.db.mongo import ponds_collection


def list_all() -> list[dict]:
    return list(ponds_collection().find({}, {"_id": 1, "name": 1, "pump": 1, "water_node": 1}))


def set_pump_failed(pond_id: str, failed: bool) -> bool:
    """Tra ve False neu khong co ao nay."""
    res = ponds_collection().update_one(
        {"_id": pond_id},
        {"$set": {"pump.failed": failed, "pump.failed_since": utcnow() if failed else None}},
    )
    return res.matched_count > 0
