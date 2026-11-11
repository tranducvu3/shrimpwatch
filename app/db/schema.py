"""Create collections and indexes, and migrate old data. Safe to run repeatedly (idempotent)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.db.mongo import get_db
from app.db.mongo import ALL, EVENTS, INCIDENTS, METRICS, METRICS_HOURLY, PONDS
from app.domain.incident_lifecycle import ACTIVE_STATES
from app.domain.models import IncidentState, Severity
from app.domain.ponds import pond_ids

logger = get_logger("db.schema")

METRICS_POND_METRIC_TS_INDEX = "pond_metric_ts"
ONE_ACTIVE_INCIDENT_INDEX = "one_active_incident_per_pond"
DUPLICATE_KEY = 11000
_OBSOLETE_METRIC_INDEXES = [
    "metadata.pond_id_1_timestamp_-1",
    "metadata.metric_1_timestamp_-1",
    "metadata.device_type_1_timestamp_-1",
]


# Root causes renamed to one `<metric>_out_of_range` per metric: old name -> new name
LEGACY_ROOT_CAUSES = {
    "do_anomaly_drop": "do_out_of_range",
    "do_critical": "do_out_of_range",
    "do_low": "do_out_of_range",
    "salinity_low": "salinity_out_of_range",
    "nh3_high": "nh3_out_of_range",
    "nitrite_high": "nitrite_out_of_range",
    "tan_high": "tan_out_of_range",
    "co2_high": "co2_out_of_range",
    "orp_low": "orp_out_of_range",
    "alkalinity_low": "alkalinity_out_of_range",
}
_EVENT_ROOT_CAUSE_FIELDS = ("root_cause", "from_root_cause", "to_root_cause")


def migrate_root_causes(db: Database) -> int:
    """Rename old root causes in incidents and events. Returns documents changed."""
    changed = 0
    for old, new in LEGACY_ROOT_CAUSES.items():
        changed += db[INCIDENTS].update_many({"root_cause": old}, {"$set": {"root_cause": new}}).modified_count
        for field in _EVENT_ROOT_CAUSE_FIELDS:
            changed += (
                db[EVENTS].update_many({f"details.{field}": old}, {"$set": {f"details.{field}": new}}).modified_count
            )
    return changed


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


# $jsonSchema validators: MongoDB rejects a write that would break the shape the services and the dashboard rely on.
# Only the fields every reader needs are required; new optional fields need no change here.
_DATE = {"bsonType": "date"}
_STRING = {"bsonType": "string"}
_OPTIONAL_STRING = {"bsonType": ["string", "null"]}
INCIDENT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "farm_id", "pond_id", "state", "severity", "title", "created_at", "updated_at"],
        "properties": {
            "_id": _STRING,
            "farm_id": _STRING,
            "pond_id": _STRING,
            "state": {"enum": [s.value for s in IncidentState]},
            "severity": {"enum": [s.value for s in Severity]},
            "title": _STRING,
            "description": _STRING,
            "root_cause": _OPTIONAL_STRING,
            "created_at": _DATE,
            "updated_at": _DATE,
            "acknowledged_at": {"bsonType": ["date", "null"]},
            "recovered_at": {"bsonType": ["date", "null"]},
            "fault_started_at": {"bsonType": ["date", "null"]},
            "detection_delay_sec": {"bsonType": ["double", "int", "long", "null"]},
            "evidence": {"bsonType": "object"},
            "actions": {"bsonType": "array"},
            "remedies": {"bsonType": "array", "items": _STRING},
        },
    }
}
EVENT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["timestamp", "event", "pond_id"],
        "properties": {
            "timestamp": _DATE,
            "event": _STRING,
            "pond_id": _OPTIONAL_STRING,
            "incident_id": _STRING,
            "details": {"bsonType": "object"},
        },
    }
}


def _set_validator(db: Database, name: str, validator: dict) -> bool:
    """Apply a validator to a collection. `moderate`: documents written before it are not rejected on update.

    collMod needs a dbAdmin-like role; without it the app still runs (the services keep the shape), so only warn.
    Undo with collMod validator={} if needed. Returns True when applied.
    """
    try:
        db.command("collMod", name, validator=validator, validationLevel="moderate", validationAction="error")
    except OperationFailure as e:
        logger.warning("validator not applied", extra={"collection": name, "err": str(e)})
        return False
    return True


def _init_incidents(db: Database, force: bool) -> None:
    _ensure_collection(db, INCIDENTS, force)
    _set_validator(db, INCIDENTS, INCIDENT_VALIDATOR)
    incidents = db[INCIDENTS]
    incidents.create_index([("pond_id", 1), ("state", 1)])
    incidents.create_index([("state", 1), ("created_at", -1)])
    incidents.create_index([("created_at", -1)])
    # Incidents recovered in the last 24 hours, listed by the worker every cycle for the Datadog table
    incidents.create_index([("state", 1), ("recovered_at", -1)])
    # At most one active incident per pond, enforced by MongoDB: the ingest check and the worker can both open one.
    try:
        incidents.create_index(
            [("pond_id", 1)],
            name=ONE_ACTIVE_INCIDENT_INDEX,
            unique=True,
            partialFilterExpression={"state": {"$in": [s.value for s in ACTIVE_STATES]}},
        )
    except OperationFailure as e:
        if e.code != DUPLICATE_KEY:
            raise
        # Old data with 2 active incidents for a pond: keep starting (the service still updates the existing one),
        # but say so loudly, the invariant is not enforced until those incidents are closed
        logger.error("one-active-incident index not created: a pond has 2 active incidents", extra={"err": str(e)})


# The similar-incident search (Atlas Vector Search on incidents.fingerprint) was removed.
OBSOLETE_INCIDENT_SEARCH_INDEX = "incident_fingerprint"
OBSOLETE_INCIDENT_FIELDS = ("fingerprint", "similar_incidents")


def remove_similar_incident_search(db: Database) -> int:
    """Drop the old vector search index and unset its fields on incidents. Returns incidents changed. Idempotent."""
    incidents = db[INCIDENTS]
    try:
        if next(iter(incidents.list_search_indexes(OBSOLETE_INCIDENT_SEARCH_INDEX)), None):
            incidents.drop_search_index(OBSOLETE_INCIDENT_SEARCH_INDEX)
    except OperationFailure:
        pass  # Plain mongod has no search indexes
    query = {"$or": [{field: {"$exists": True}} for field in OBSOLETE_INCIDENT_FIELDS]}
    return incidents.update_many(query, {"$unset": dict.fromkeys(OBSOLETE_INCIDENT_FIELDS, "")}).modified_count


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
    _set_validator(db, EVENTS, EVENT_VALIDATOR)
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
    result["similar_incident_fields_removed"] = remove_similar_incident_search(db)
    result["ponds"] = _init_ponds(db, force)
    _init_events(db, force)
    result["events"] = "ready"
    result["root_causes_migrated"] = migrate_root_causes(db)
    result["collection_infos"] = describe_collections(db)
    logger.info("Collections initialized", extra={"ctx": result})
    return result
