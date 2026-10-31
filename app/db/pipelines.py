"""Cac pipeline aggregation. Ham thuan: chi dung list stage, khong chay query."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.clock import utcnow
from app.db.mongo import EVENTS, METRICS_HOURLY


def compute_do_drop_rate_pipeline(
    pond_ids: list[str], window_sec: int = 300, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Toc do sut DO cua nhieu ao trong 1 lan aggregate.

    Cua so theo thoi gian (range + unit), khong theo so ban ghi: cam bien rot goi
    thi cua so van dung 5 phut, khong keo dai ra.
    """
    now = now or utcnow()
    window = {"range": [-window_sec, 0], "unit": "second"}

    return [
        {
            "$match": {
                "metadata.pond_id": {"$in": pond_ids},
                "metadata.metric": "do",
                "timestamp": {"$gte": now - timedelta(seconds=window_sec), "$lte": now},
                "metadata.source": "sensor",
            }
        },
        {
            "$setWindowFields": {
                "partitionBy": "$metadata.pond_id",
                "sortBy": {"timestamp": 1},
                "output": {
                    # mg/L moi phut; am khi DO giam
                    "do_rate": {"$derivative": {"input": "$value", "unit": "minute"}, "window": window},
                    "first_do": {"$first": "$value", "window": window},
                    "window_start_ts": {"$first": "$timestamp", "window": window},
                    "min_do": {"$min": "$value", "window": window},
                },
            }
        },
        # Moi ao chi giu diem moi nhat: do la ket qua cua cua so 5 phut ket thuc "bay gio".
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$metadata.pond_id", "latest": {"$first": "$$ROOT"}}},
        {"$replaceWith": "$latest"},
        {
            "$project": {
                "_id": 0,
                "pond_id": "$metadata.pond_id",
                "window_start_ts": 1,
                "window_end_ts": "$timestamp",
                "first_do": 1,
                "last_do": "$value",
                "min_do": 1,
                "do_drop_mg_per_l": {"$subtract": ["$first_do", "$value"]},
                "delta_minutes": {"$divide": [{"$subtract": ["$timestamp", "$window_start_ts"]}, 60000.0]},
                # $derivative tra ve null khi cua so chi co 1 diem
                "do_drop_rate_mg_per_l_per_min": {"$ifNull": [{"$multiply": ["$do_rate", -1]}, 0.0]},
            }
        },
    ]


def rollup_hourly_pipeline(since: datetime, until: datetime | None = None) -> list[dict[str, Any]]:
    """Gop du lieu tho thanh min/max/avg theo gio va $merge vao metrics_hourly.

    Tinh lai tron ven cac gio tu `since` (den het gio chua `until`) nen whenMatched=replace
    la dung (idempotent).
    """
    time_range: dict[str, datetime] = {"$gte": since.replace(minute=0, second=0, microsecond=0)}
    if until is not None:
        time_range["$lt"] = until.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return [
        {"$match": {"timestamp": time_range}},
        {
            "$group": {
                "_id": {
                    "pond_id": "$metadata.pond_id",
                    "metric": "$metadata.metric",
                    "hour": {"$dateTrunc": {"date": "$timestamp", "unit": "hour"}},
                },
                "farm_id": {"$first": "$metadata.farm_id"},
                "source": {"$first": "$metadata.source"},
                "unit": {"$first": "$metadata.unit"},
                "min": {"$min": "$value"},
                "max": {"$max": "$value"},
                "avg": {"$avg": "$value"},
                "count": {"$sum": 1},
            }
        },
        {
            "$project": {
                "_id": 0,
                "pond_id": "$_id.pond_id",
                "metric": "$_id.metric",
                "hour": "$_id.hour",
                "farm_id": 1,
                "source": 1,
                "unit": 1,
                "min": 1,
                "max": 1,
                "avg": {"$round": ["$avg", 4]},
                "count": 1,
                "rolled_up_at": "$$NOW",
            }
        },
        {
            "$merge": {
                "into": METRICS_HOURLY,
                "on": ["pond_id", "metric", "hour"],
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]


def compute_ph_swing_pipeline(pond_id: str, hours: int = 24, now: datetime | None = None) -> list[dict[str, Any]]:
    """Bien do pH 24h doc tu metrics_hourly (~24 dong) thay vi ~2.880 diem tho."""
    since = ((now or utcnow()) - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    return [
        {"$match": {"pond_id": pond_id, "metric": "ph", "source": "sensor", "hour": {"$gte": since}}},
        {
            "$group": {
                "_id": "$pond_id",
                "ph_max": {"$max": "$max"},
                "ph_min": {"$min": "$min"},
                "samples": {"$sum": "$count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "pond_id": "$_id",
                "ph_max": 1,
                "ph_min": 1,
                "samples": 1,
                "ph_swing": {"$round": [{"$subtract": ["$ph_max", "$ph_min"]}, 3]},
            }
        },
    ]


def incident_timeline_pipeline(incident_id: str, lead_minutes: int = 30) -> list[dict[str, Any]]:
    """Su co + toan bo event cua no + su kien quat cua ao truoc/trong luc su co."""
    return [
        {"$match": {"_id": incident_id}},
        {
            "$lookup": {
                "from": EVENTS,
                "localField": "_id",
                "foreignField": "incident_id",
                "as": "incident_events",
            }
        },
        {
            "$lookup": {
                "from": EVENTS,
                "let": {
                    "pond": "$pond_id",
                    "from_ts": {
                        "$dateSubtract": {"startDate": "$created_at", "unit": "minute", "amount": lead_minutes}
                    },
                    "to_ts": "$updated_at",
                },
                "pipeline": [
                    {
                        "$match": {
                            "event": "pump_state_change",
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$pond_id", "$$pond"]},
                                    {"$gte": ["$timestamp", "$$from_ts"]},
                                    {"$lte": ["$timestamp", "$$to_ts"]},
                                ]
                            },
                        }
                    },
                ],
                "as": "pump_events",
            }
        },
        {
            "$set": {
                "timeline": {
                    "$sortArray": {
                        "input": {"$concatArrays": ["$incident_events", "$pump_events"]},
                        "sortBy": {"timestamp": 1},
                    }
                }
            }
        },
        {"$unset": ["incident_events", "pump_events", "timeline._id"]},
    ]
