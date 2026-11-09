"""Aggregation pipelines. Pure functions: they only build the stage list and never run a query."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.clock import utcnow
from app.db.mongo import EVENTS, INCIDENTS, METRICS_HOURLY


def compute_do_drop_rate_pipeline(
    pond_ids: list[str], window_sec: int = 300, now: datetime | None = None
) -> list[dict[str, Any]]:
    """DO drop rate for many ponds in a single aggregate.

    The window is time-based (range + unit), not record-based: if a sensor drops packets
    the window is still exactly 5 minutes and does not stretch.
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
                    # mg/L per minute; negative when DO falls
                    "do_rate": {"$derivative": {"input": "$value", "unit": "minute"}, "window": window},
                    "first_do": {"$first": "$value", "window": window},
                    "window_start_ts": {"$first": "$timestamp", "window": window},
                    "min_do": {"$min": "$value", "window": window},
                },
            }
        },
        # Keep only the latest point per pond: that is the result of the 5-minute window ending "now".
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
                # $derivative returns null when the window has only 1 point
                "do_drop_rate_mg_per_l_per_min": {"$ifNull": [{"$multiply": ["$do_rate", -1]}, 0.0]},
            }
        },
    ]


def rollup_hourly_pipeline(since: datetime, until: datetime | None = None) -> list[dict[str, Any]]:
    """Roll raw data up into hourly min/max/avg and $merge into metrics_hourly.

    Recomputes whole hours from `since` (through the end of the hour containing `until`), so
    whenMatched=replace is correct (idempotent).
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
    """24h pH swing read from metrics_hourly (~24 rows) instead of ~2,880 raw points."""
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


# Pond events that explain an incident: what broke (pump, condition, scenario) and what the farmer did (remedy)
POND_TIMELINE_EVENTS = [
    "pump_state_change",
    "condition_start",
    "condition_clear",
    "scenario_start",
    "scenario_clear",
    "remedy_applied",
]


def incident_timeline_pipeline(incident_id: str, lead_minutes: int = 30) -> list[dict[str, Any]]:
    """The incident + all its events + the pond's events (pump, conditions, remedies) before/during the incident."""
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
                    # Active incident: up to now, so a remedy written just after the transition still shows;
                    # recovered: up to its last change
                    "to_ts": {"$cond": [{"$eq": ["$state", "recovered"]}, "$updated_at", "$$NOW"]},
                },
                "pipeline": [
                    {
                        "$match": {
                            "event": {"$in": POND_TIMELINE_EVENTS},
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


def latest_readings_pipeline(pond_ids: list[str], metrics: list[str], since: datetime) -> list[dict[str, Any]]:
    """Latest value of each (pond, metric) since `since`, for the control panel.

    Filtering on pond_id + metric + timestamp matches the pond_metric_ts index; `since` bounds the records scanned.
    """
    return [
        {
            "$match": {
                "metadata.pond_id": {"$in": pond_ids},
                "metadata.metric": {"$in": metrics},
                "timestamp": {"$gte": since},
            }
        },
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": {"pond_id": "$metadata.pond_id", "metric": "$metadata.metric"},
                "value": {"$first": "$value"},
                "unit": {"$first": "$metadata.unit"},
                "timestamp": {"$first": "$timestamp"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "pond_id": "$_id.pond_id",
                "metric": "$_id.metric",
                "value": 1,
                "unit": 1,
                "timestamp": 1,
            }
        },
    ]


def incident_changes_pipeline() -> list[dict[str, Any]]:
    """Change stream filter: new or changed incidents and new events, with only the fields the panel needs.

    `metrics` is a time series collection, which change streams do not support; the panel polls readings instead.
    """
    return [
        {
            "$match": {
                "$or": [
                    {"ns.coll": INCIDENTS, "operationType": {"$in": ["insert", "update", "replace"]}},
                    {"ns.coll": EVENTS, "operationType": "insert"},
                ]
            }
        },
        {
            "$project": {
                "ns.coll": 1,
                "operationType": 1,
                "documentKey": 1,
                "fullDocument.pond_id": 1,
                "fullDocument.state": 1,
                "fullDocument.severity": 1,
                "fullDocument.incident_id": 1,
                "fullDocument.event": 1,
            }
        },
    ]
