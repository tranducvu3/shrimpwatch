from __future__ import annotations

import time
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from pymongo.errors import OperationFailure

from app.config import settings
from app.logging_setup import get_logger

logger = get_logger("db")

_TS_META = ["farm_id", "pond_id", "device_id", "device_type", "metric", "source", "unit"]


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
    coll.create_index([("metadata.pond_id", 1), ("timestamp", -1)])
    coll.create_index([("metadata.metric", 1), ("timestamp", -1)])
    coll.create_index([("metadata.device_type", 1), ("timestamp", -1)])
    result["metrics_indexes"] = True

    if "incidents" not in db.list_collection_names() or force:
        if force and "incidents" in db.list_collection_names():
            db["incidents"].drop()
        db.create_collection("incidents")
    incidents = incidents_collection()
    incidents.create_index([("pond_id", 1), ("state", 1)])
    incidents.create_index([("created_at", -1)])
    result["incidents"] = "ready"

    if "ponds" not in db.list_collection_names() or force:
        if force and "ponds" in db.list_collection_names():
            db["ponds"].drop()
        db.create_collection("ponds")
    ponds = ponds_collection()
    for i in range(1, settings.NUM_PONDS + 1):
        pond_id = f"pond-{i:02d}"
        ponds.update_one(
            {"_id": pond_id},
            {
                "$setOnInsert": {
                    "_id": pond_id,
                    "farm_id": settings.FARM_ID,
                    "name": f"Ao {i}",
                    "created_at": int(time.time() * 1000),
                    "pump": {"id": f"pump-{pond_id}", "failed": False, "power_watts": 0.0},
                    "water_node": {"id": f"node-{pond_id}"},
                }
            },
            upsert=True,
        )
    result["ponds"] = list(ponds.find({}, {"_id": 1, "name": 1}))

    if "events" not in db.list_collection_names() or force:
        if force and "events" in db.list_collection_names():
            db["events"].drop()
        db.create_collection("events")
    events = events_collection()
    events.create_index([("pond_id", 1), ("timestamp", -1)])
    result["events"] = "ready"

    # Khong dung $listCatalog (can quyen cao) -> thay = listCollections (readWrite/dbOwner deu duoc)
    collection_infos = []
    for coll_name in ["metrics", "incidents", "ponds", "events"]:
        try:
            info_cursor = db.command(
                "listCollections",
                1,
                filter={"name": coll_name}
            )
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


def compute_do_drop_rate_pipeline(pond_id: str, window_sec: int = 300) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    start = now - timedelta(seconds=max(window_sec * 3, 1800))

    pipeline = [
        {
            "$match": {
                "timestamp": {"$gte": start, "$lte": now},
                "metadata.pond_id": pond_id,
                "metadata.metric": "do",
                "metadata.source": {"$in": ["sensor"]},
            }
        },
        {"$sort": {"timestamp": 1}},
        {
            "$setWindowFields": {
                "partitionBy": "$metadata.pond_id",
                "sortBy": {"timestamp": 1},
                "output": {
                    "first_do_window": {
                        "$first": "$value",
                        "window": {
                            "documents": [-max(1, window_sec // settings.SIMULATOR_INTERVAL_SEC), 0]
                        },
                    },
                    "last_do_window": {"$last": "$value", "window": {"documents": [-0, 0]}},
                    "first_ts_window": {
                        "$first": "$timestamp",
                        "window": {
                            "documents": [-max(1, window_sec // settings.SIMULATOR_INTERVAL_SEC), 0]
                        },
                    },
                    "last_ts_window": {
                        "$last": "$timestamp",
                        "window": {"documents": [-0, 0]}
                    },
                    "min_do_window": {
                        "$min": "$value",
                        "window": {
                            "documents": [-max(1, window_sec // settings.SIMULATOR_INTERVAL_SEC), 0]
                        },
                    },
                },
            }
        },
        {"$match": {"first_ts_window": {"$exists": True}, "last_ts_window": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "pond_id": "$metadata.pond_id",
                "window_start_ts": "$first_ts_window",
                "window_end_ts": "$last_ts_window",
                "first_do": "$first_do_window",
                "last_do": "$last_do_window",
                "min_do": "$min_do_window",
                "do_drop_mg_per_l": {"$subtract": ["$first_do_window", "$last_do_window"]},
                "delta_minutes": {
                    "$divide": [
                        {"$subtract": [{"$toLong": "$last_ts_window"}, {"$toLong": "$first_ts_window"}]},
                        60000.0,
                    ]
                },
            }
        },
        {
            "$project": {
                "pond_id": 1,
                "window_start_ts": 1,
                "window_end_ts": 1,
                "first_do": 1,
                "last_do": 1,
                "min_do": 1,
                "do_drop_mg_per_l": 1,
                "delta_minutes": 1,
                "do_drop_rate_mg_per_l_per_min": {
                    "$cond": [
                        {"$gt": ["$delta_minutes", 0]},
                        {"$divide": ["$do_drop_mg_per_l", "$delta_minutes"]},
                        0.0,
                    ]
                },
            }
        },
    ]
    return pipeline


def compute_ph_swing_pipeline(pond_id: str, hours: int = 24) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    return [
        {
            "$match": {
                "timestamp": {"$gte": now - timedelta(hours=hours), "$lte": now},
                "metadata.pond_id": pond_id,
                "metadata.metric": "ph",
                "metadata.source": "sensor",
            }
        },
        {
            "$group": {
                "_id": "$metadata.pond_id",
                "ph_max": {"$max": "$value"},
                "ph_min": {"$min": "$value"},
                "samples": {"$sum": 1},
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
