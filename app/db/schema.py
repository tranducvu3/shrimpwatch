"""Create collections and indexes, and migrate old data. Safe to run repeatedly (idempotent)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.db.mongo import get_db
from app.db.mongo import ALL, EVENTS, INCIDENTS, METRICS, METRICS_HOURLY, PONDS
from app.db.pipelines import INCIDENT_VECTOR_INDEX
from app.domain.incident_fingerprint import DIMENSIONS
from app.domain.ponds import pond_ids

logger = get_logger("db.schema")

METRICS_POND_METRIC_TS_INDEX = "pond_metric_ts"
_OBSOLETE_METRIC_INDEXES = [
    "metadata.pond_id_1_timestamp_-1",
    "metadata.metric_1_timestamp_-1",
    "metadata.device_type_1_timestamp_-1",
]


def _ensure_collection(db: Database, name: str, force: bool, **options: Any) -> bool:
    """Create the collection if missing (force: drop and recreate). Returns True if it was created."""
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
    # ESR: every worker query is pond_id (=) + metric (=) + timestamp (range/sort)
    # -> 1 compound index replaces the 3 old single-field indexes (the planner could only use 1 of them).
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
    # Unique index: the $merge target (on: pond_id + metric + hour), also serves history queries.
    hourly.create_index([("pond_id", 1), ("metric", 1), ("hour", -1)], unique=True)
    # "Warm" data is kept for 1 year; raw data in metrics is kept for only 7 days.
    hourly.create_index([("hour", 1)], expireAfterSeconds=settings.ROLLUP_TTL_SECONDS)


def _init_incidents(db: Database, force: bool) -> None:
    _ensure_collection(db, INCIDENTS, force)
    incidents = db[INCIDENTS]
    incidents.create_index([("pond_id", 1), ("state", 1)])
    incidents.create_index([("state", 1), ("created_at", -1)])
    incidents.create_index([("created_at", -1)])


def incident_vector_index_definition() -> dict[str, Any]:
    return {
        "fields": [
            {"type": "vector", "path": "fingerprint", "numDimensions": DIMENSIONS, "similarity": "euclidean"},
            {"type": "filter", "path": "state"},
        ]
    }


def _init_incident_vector_index(db: Database) -> str:
    """Atlas Vector Search index on incidents.fingerprint. Plain mongod does not support it -> skipped."""
    incidents = db[INCIDENTS]
    definition = incident_vector_index_definition()
    try:
        existing = next(iter(incidents.list_search_indexes(INCIDENT_VECTOR_INDEX)), None)
        if existing:
            # Compare only "fields": Atlas may return extra keys -> avoids rebuilding the index on every init
            if (existing.get("latestDefinition") or {}).get("fields") != definition["fields"]:
                incidents.update_search_index(INCIDENT_VECTOR_INDEX, definition)
                return "updated"
            return "existed"
        incidents.create_search_index(
            SearchIndexModel(definition=definition, name=INCIDENT_VECTOR_INDEX, type="vectorSearch")
        )
        return "created"
    except OperationFailure as e:
        logger.warning("Vector search index unavailable, using aggregation fallback", extra={"err": str(e)})
        return "unsupported"


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
    # Older versions stored created_at as epoch ms -> convert to BSON Date like the other collections.
    ponds.update_many({"created_at": {"$type": "number"}}, [{"$set": {"created_at": {"$toDate": "$created_at"}}}])
    return list(ponds.find({}, {"_id": 1, "name": 1}))


def _init_events(db: Database, force: bool) -> None:
    _ensure_collection(db, EVENTS, force)
    events = db[EVENTS]
    # Older versions stored incident_action with details.incident_id -> move it to top level like other events.
    events.update_many(
        {"details.incident_id": {"$exists": True}, "incident_id": {"$exists": False}},
        [{"$set": {"incident_id": "$details.incident_id"}}, {"$unset": "details.incident_id"}],
    )
    events.create_index([("pond_id", 1), ("timestamp", -1)])
    events.create_index([("incident_id", 1), ("timestamp", 1)])


def describe_collections(db: Database) -> list[dict[str, Any]]:
    """Time series/TTL info for each collection.

    Avoids $listCatalog (needs elevated rights) -> uses listCollections (readWrite/dbOwner are enough),
    then reshapes it like $listCatalog output: md.timeseries + md.options.expireAfterSeconds.
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
    result["incident_vector_index"] = _init_incident_vector_index(db)
    result["ponds"] = _init_ponds(db, force)
    _init_events(db, force)
    result["events"] = "ready"
    result["collection_infos"] = describe_collections(db)
    logger.info("Collections initialized", extra={"ctx": result})
    return result
