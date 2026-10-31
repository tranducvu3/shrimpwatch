"""Tao collection, index va migrate du lieu cu. Chay lai nhieu lan van an toan (idempotent)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.db.mongo import get_db
from app.db.mongo import ALL, EVENTS, INCIDENTS, METRICS, METRICS_HOURLY, PONDS
from app.domain.ponds import pond_ids

logger = get_logger("db.schema")

METRICS_POND_METRIC_TS_INDEX = "pond_metric_ts"
_OBSOLETE_METRIC_INDEXES = [
    "metadata.pond_id_1_timestamp_-1",
    "metadata.metric_1_timestamp_-1",
    "metadata.device_type_1_timestamp_-1",
]


def _ensure_collection(db: Database, name: str, force: bool, **options: Any) -> bool:
    """Tao collection neu chua co (force: xoa roi tao lai). Tra ve True neu vua tao."""
    exists = name in db.list_collection_names()
    if exists and not force:
        return False
    if exists:
        db[name].drop()
    db.create_collection(name, **options)
    return True


def _init_metrics(db: Database, force: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        created = _ensure_collection(
            db,
            METRICS,
            force,
            timeseries={"timeField": "timestamp", "metaField": "metadata", "granularity": "seconds"},
            expireAfterSeconds=settings.TS_TTL_SECONDS,
        )
        if created:
            result["metrics_created"] = True
    except OperationFailure as e:
        if "already exists" in str(e).lower() or "TimeSeriesOptions are immutable" in str(e):
            result["metrics_created"] = "existed"
        else:
            raise

    coll = db[METRICS]
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
    return result


def _init_metrics_hourly(db: Database, force: bool) -> None:
    _ensure_collection(db, METRICS_HOURLY, force)
    hourly = db[METRICS_HOURLY]
    # Unique index: dich cua $merge (on: pond_id + metric + hour) va phuc vu truy van lich su.
    hourly.create_index([("pond_id", 1), ("metric", 1), ("hour", -1)], unique=True)
    # Du lieu "am" giu 1 nam, du lieu tho trong metrics chi giu 7 ngay.
    hourly.create_index([("hour", 1)], expireAfterSeconds=settings.ROLLUP_TTL_SECONDS)


def _init_incidents(db: Database, force: bool) -> None:
    _ensure_collection(db, INCIDENTS, force)
    incidents = db[INCIDENTS]
    incidents.create_index([("pond_id", 1), ("state", 1)])
    incidents.create_index([("state", 1), ("created_at", -1)])
    incidents.create_index([("created_at", -1)])


def _init_ponds(db: Database, force: bool) -> list[dict]:
    _ensure_collection(db, PONDS, force)
    ponds = db[PONDS]
    for i, pid in enumerate(pond_ids(), start=1):
        ponds.update_one(
            {"_id": pid},
            {
                "$setOnInsert": {
                    "_id": pid,
                    "farm_id": settings.FARM_ID,
                    "name": f"Ao {i}",
                    "created_at": utcnow(),
                    "pump": {"id": f"pump-{pid}", "failed": False, "power_watts": 0.0},
                    "water_node": {"id": f"node-{pid}"},
                }
            },
            upsert=True,
        )
    # Ban cu luu created_at dang epoch ms -> chuyen ve BSON Date nhu cac collection khac.
    ponds.update_many({"created_at": {"$type": "number"}}, [{"$set": {"created_at": {"$toDate": "$created_at"}}}])
    return list(ponds.find({}, {"_id": 1, "name": 1}))


def _init_events(db: Database, force: bool) -> None:
    _ensure_collection(db, EVENTS, force)
    events = db[EVENTS]
    # Ban cu luu incident_action voi details.incident_id -> dua len top-level nhu cac event khac.
    events.update_many(
        {"details.incident_id": {"$exists": True}, "incident_id": {"$exists": False}},
        [{"$set": {"incident_id": "$details.incident_id"}}, {"$unset": "details.incident_id"}],
    )
    events.create_index([("pond_id", 1), ("timestamp", -1)])
    events.create_index([("incident_id", 1), ("timestamp", 1)])


def describe_collections(db: Database) -> list[dict[str, Any]]:
    """Thong tin timeseries/TTL tung collection.

    Khong dung $listCatalog (can quyen cao) -> listCollections (readWrite/dbOwner deu duoc),
    roi dua ve dang giong output $listCatalog: md.timeseries + md.options.expireAfterSeconds.
    """
    infos = []
    for name in ALL:
        try:
            batch = db.command("listCollections", 1, filter={"name": name}).get("cursor", {}).get("firstBatch", [])
        except Exception as e:
            logger.warning("listCollections failed for " + name, extra={"err": str(e)})
            continue
        if not batch:
            continue
        opts = batch[0].get("options", {})
        md: dict[str, Any] = {}
        if opts.get("timeseries"):
            md["timeseries"] = opts["timeseries"]
        if "expireAfterSeconds" in opts:
            md.setdefault("options", {})["expireAfterSeconds"] = opts["expireAfterSeconds"]
        infos.append({"name": batch[0].get("name"), "md": md or {"options": opts}})
    return infos


def init_collections(force: bool = False) -> dict[str, Any]:
    db = get_db()
    result = _init_metrics(db, force)
    _init_metrics_hourly(db, force)
    result["metrics_hourly"] = "ready"
    _init_incidents(db, force)
    result["incidents"] = "ready"
    result["ponds"] = _init_ponds(db, force)
    _init_events(db, force)
    result["events"] = "ready"
    result["collection_infos"] = describe_collections(db)
    logger.info("Collections initialized", extra={"ctx": result})
    return result
