from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from pymongo.errors import OperationFailure

from app.core.config import settings
from app.core.log import get_logger
from app.domain.ponds import pond_ids

logger = get_logger("db")

_TS_META = ["farm_id", "pond_id", "device_id", "device_type", "metric", "source", "unit"]

METRICS_POND_METRIC_TS_INDEX = "pond_metric_ts"
_OBSOLETE_METRIC_INDEXES = [
    "metadata.pond_id_1_timestamp_-1",
    "metadata.metric_1_timestamp_-1",
    "metadata.device_type_1_timestamp_-1",
]


_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        _client.admin.command("ping")
    return _client


def get_db() -> Database:
    return get_client()[settings.MONGODB_DB]


def metrics_collection() -> Collection:
    return get_db()["metrics"]


def metrics_hourly_collection() -> Collection:
    return get_db()["metrics_hourly"]


def incidents_collection() -> Collection:
    return get_db()["incidents"]


def ponds_collection() -> Collection:
    return get_db()["ponds"]


def events_collection() -> Collection:
    return get_db()["events"]


def init_collections(force: bool = False) -> dict[str, Any]:
    db = get_db()
    result: dict[str, Any] = {}

    if "metrics" not in db.list_collection_names() or force:
        if force and "metrics" in db.list_collection_names():
            db["metrics"].drop()
        try:
            db.create_collection(
                "metrics",
                timeseries={
                    "timeField": "timestamp",
                    "metaField": "metadata",
                    "granularity": "seconds",
                },
                expireAfterSeconds=settings.TS_TTL_SECONDS,
            )
            result["metrics_created"] = True
        except OperationFailure as e:
            if "already exists" in str(e).lower() or "TimeSeriesOptions are immutable" in str(e):
                result["metrics_created"] = "existed"
            else:
                raise

    coll = metrics_collection()
    # ESR: moi truy van cua worker la pond_id (=) + metric (=) + timestamp (range/sort)
    # -> 1 index ghep thay cho 3 index don le cu (planner chi dung duoc 1 trong so do).
    existing = coll.index_information()
    for name in _OBSOLETE_METRIC_INDEXES:
        if name in existing:
            coll.drop_index(name)
    coll.create_index(
        [("metadata.pond_id", 1), ("metadata.metric", 1), ("timestamp", -1)],
        name=METRICS_POND_METRIC_TS_INDEX,
    )
    result["metrics_indexes"] = True

    if "metrics_hourly" not in db.list_collection_names() or force:
        if force and "metrics_hourly" in db.list_collection_names():
            db["metrics_hourly"].drop()
        db.create_collection("metrics_hourly")
    hourly = metrics_hourly_collection()
    # Unique index: dich cua $merge (on: pond_id + metric + hour) va phuc vu truy van lich su.
    hourly.create_index([("pond_id", 1), ("metric", 1), ("hour", -1)], unique=True)
    # Du lieu "am" giu 1 nam, du lieu tho trong metrics chi giu 7 ngay.
    hourly.create_index([("hour", 1)], expireAfterSeconds=settings.ROLLUP_TTL_SECONDS)
    result["metrics_hourly"] = "ready"

    if "incidents" not in db.list_collection_names() or force:
        if force and "incidents" in db.list_collection_names():
            db["incidents"].drop()
        db.create_collection("incidents")
    incidents = incidents_collection()
    incidents.create_index([("pond_id", 1), ("state", 1)])
    incidents.create_index([("state", 1), ("created_at", -1)])
    incidents.create_index([("created_at", -1)])
    result["incidents"] = "ready"

    if "ponds" not in db.list_collection_names() or force:
        if force and "ponds" in db.list_collection_names():
            db["ponds"].drop()
        db.create_collection("ponds")
    ponds = ponds_collection()
    for i, pond_id in enumerate(pond_ids(), start=1):
        ponds.update_one(
            {"_id": pond_id},
            {
                "$setOnInsert": {
                    "_id": pond_id,
                    "farm_id": settings.FARM_ID,
                    "name": f"Ao {i}",
                    "created_at": datetime.utcnow(),
                    "pump": {"id": f"pump-{pond_id}", "failed": False, "power_watts": 0.0},
                    "water_node": {"id": f"node-{pond_id}"},
                }
            },
            upsert=True,
        )
    # Ban cu luu created_at dang epoch ms -> chuyen ve BSON Date nhu cac collection khac.
    ponds.update_many({"created_at": {"$type": "number"}}, [{"$set": {"created_at": {"$toDate": "$created_at"}}}])
    result["ponds"] = list(ponds.find({}, {"_id": 1, "name": 1}))

    if "events" not in db.list_collection_names() or force:
        if force and "events" in db.list_collection_names():
            db["events"].drop()
        db.create_collection("events")
    events = events_collection()
    # Ban cu luu incident_action voi details.incident_id -> dua len top-level nhu cac event khac.
    events.update_many(
        {"details.incident_id": {"$exists": True}, "incident_id": {"$exists": False}},
        [{"$set": {"incident_id": "$details.incident_id"}}, {"$unset": "details.incident_id"}],
    )
    events.create_index([("pond_id", 1), ("timestamp", -1)])
    events.create_index([("incident_id", 1), ("timestamp", 1)])
    result["events"] = "ready"

    # Khong dung $listCatalog (can quyen cao) -> thay = listCollections (readWrite/dbOwner deu duoc)
    collection_infos = []
    for coll_name in ["metrics", "metrics_hourly", "incidents", "ponds", "events"]:
        try:
            info_cursor = db.command("listCollections", 1, filter={"name": coll_name})
            cursor = info_cursor.get("cursor", {})
            first_batch = cursor.get("firstBatch", [])
            if first_batch:
                entry = first_batch[0]
                # Bien doi cau truc cho giong $listCatalog output: entry.options chua timeseries + expireAfterSeconds
                md = {}
                opts = entry.get("options", {})
                if opts.get("timeseries"):
                    md["timeseries"] = opts["timeseries"]
                if "expireAfterSeconds" in opts:
                    md.setdefault("options", {})["expireAfterSeconds"] = opts["expireAfterSeconds"]
                if not md:
                    md = {"options": opts}
                collection_infos.append({"name": entry.get("name"), "md": md})
        except Exception as e:
            logger.warning("listCollections failed for " + coll_name, extra={"err": str(e)})
    result["collection_infos"] = collection_infos

    logger.info("Collections initialized", extra={"ctx": result})
    return result


def pack_ts_doc(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": rec["timestamp"],
        "metadata": {k: rec.get(k) for k in _TS_META if k in rec},
        "value": rec["value"],
    }


def unpack_ts_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = {"timestamp": doc["timestamp"], "value": doc["value"]}
    md = doc.get("metadata") or {}
    out.update(md)
    return out


def compute_do_drop_rate_pipeline(pond_ids: list[str], window_sec: int = 300) -> list[dict[str, Any]]:
    """Toc do sut DO cua nhieu ao trong 1 lan aggregate.

    Cua so theo thoi gian (range + unit), khong theo so ban ghi: cam bien rot goi
    thi cua so van dung 5 phut, khong keo dai ra.
    """
    now = datetime.utcnow()
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
                "into": "metrics_hourly",
                "on": ["pond_id", "metric", "hour"],
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]


def run_hourly_rollup(since: datetime, until: datetime | None = None) -> None:
    metrics_collection().aggregate(rollup_hourly_pipeline(since, until))


def compute_ph_swing_pipeline(pond_id: str, hours: int = 24) -> list[dict[str, Any]]:
    """Bien do pH 24h doc tu metrics_hourly (~24 dong) thay vi ~2.880 diem tho."""
    since = (datetime.utcnow() - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
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
                "from": "events",
                "localField": "_id",
                "foreignField": "incident_id",
                "as": "incident_events",
            }
        },
        {
            "$lookup": {
                "from": "events",
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
