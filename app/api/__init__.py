from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import (
    events_collection,
    incident_timeline_pipeline,
    incidents_collection,
    metrics_collection,
    pack_ts_doc,
    ponds_collection,
    run_hourly_rollup,
)
from app.logging_setup import get_logger
from app.metrics import gauge, increment, set_span_tags
from app.models import (
    AlkalinityInput,
    BatchIngestRequest,
    IncidentState,
    MANUAL_METRIC_UNITS,
    ManualInput,
    MetricRecord,
    MetricType,
    Source,
    DeviceType,
)

logger = get_logger("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ShrimpWatch Water Quality Ingest API",
        version="1.0.0",
        description="Ingests sensor/manual/derived metrics for 6 shrimp ponds, emits Datadog metrics & traces.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()


@app.on_event("startup")
def _startup():
    from app.db import init_collections
    from app.metrics import init_metrics

    init_metrics()
    try:
        init_collections()
    except Exception as e:
        logger.warning("init_collections skipped (expected if DB not ready yet)", extra={"err": str(e)})


@app.get("/healthz")
def healthz():
    from app.db import get_client

    try:
        get_client().admin.command("ping")
        mongo_ok = True
    except Exception as e:
        mongo_ok = False
        logger.error("Mongo ping failed", extra={"err": str(e)})
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "farm_id": settings.FARM_ID,
        "env": settings.DD_ENV,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


@app.post("/ingest/batch", status_code=202)
def ingest_batch(body: BatchIngestRequest) -> dict:
    increment("ingest.batches", tags={"batch_size": len(body.records)})
    docs, pond_ids, metrics_seen = [], set(), set()

    for r in body.records:
        doc = pack_ts_doc(
            {
                "timestamp": r.timestamp,
                "farm_id": r.farm_id,
                "pond_id": r.pond_id,
                "device_id": r.device_id,
                "device_type": r.device_type.value,
                "metric": r.metric.value,
                "source": r.source.value,
                "unit": r.unit,
                "value": r.value,
            }
        )
        docs.append(doc)
        pond_ids.add(r.pond_id)
        metrics_seen.add(r.metric.value)
        set_span_tags(pond_id=r.pond_id, device_type=r.device_type.value, metric=r.metric.value)
        gauge(
            f"metric.{r.metric.value}",
            r.value,
            tags={"pond": r.pond_id, "device_type": r.device_type.value, "source": r.source.value},
        )

    try:
        if docs:
            metrics_collection().insert_many(docs, ordered=False)
    except Exception as e:
        logger.error("ingest insert_many failed", extra={"err": str(e)})
        raise HTTPException(500, detail=f"mongo insert failed: {e}")

    # Du lieu tre/backfill nam ngoai cua so rollup 1 gio cua worker -> tu rollup cac gio no cham toi.
    timestamps = [_naive_utc(r.timestamp) for r in body.records]
    if min(timestamps) < datetime.utcnow() - timedelta(hours=1):
        run_hourly_rollup(min(timestamps), max(timestamps))

    increment("ingest.records", value=len(docs))
    logger.info(
        "ingested batch",
        extra={
            "count": len(docs),
            "ponds": sorted(pond_ids),
            "metrics": sorted(metrics_seen),
        },
    )
    return {"ingested": len(docs), "ponds": sorted(pond_ids)}


@app.post("/ingest/manual/alkalinity", status_code=202)
def ingest_alkalinity(body: AlkalinityInput) -> dict:
    return ingest_manual(MetricType.ALKALINITY, ManualInput(**body.model_dump()))


@app.post("/ingest/manual/{metric}", status_code=202)
def ingest_manual(metric: MetricType, body: ManualInput) -> dict:
    if metric not in MANUAL_METRIC_UNITS:
        raise HTTPException(422, detail=f"metric must be one of {[m.value for m in MANUAL_METRIC_UNITS]}")
    set_span_tags(pond_id=body.pond_id, device_type=DeviceType.MANUAL.value, metric=metric.value)
    record = MetricRecord(
        timestamp=body.timestamp,
        farm_id=body.farm_id,
        pond_id=body.pond_id,
        device_id=body.tester_id,
        device_type=DeviceType.MANUAL,
        metric=metric,
        value=body.value,
        source=Source.MANUAL,
        unit=MANUAL_METRIC_UNITS[metric],
    )
    return ingest_batch(BatchIngestRequest(records=[record]))


@app.get("/ponds")
def list_ponds():
    return list(ponds_collection().find({}, {"_id": 1, "name": 1, "pump": 1, "water_node": 1}))


@app.post("/ponds/{pond_id}/pump/fail")
def mark_pump_failed(pond_id: str, failed: bool = True):
    set_span_tags(pond_id=pond_id, device_type=DeviceType.PUMP.value)
    res = ponds_collection().update_one(
        {"_id": pond_id},
        {"$set": {"pump.failed": failed, "pump.failed_since": datetime.utcnow() if failed else None}},
    )
    events_collection().insert_one(
        {
            "timestamp": datetime.utcnow(),
            "pond_id": pond_id,
            "event": "pump_state_change",
            "details": {"failed": failed},
        }
    )
    increment("pump.failure", tags={"pond": pond_id, "state": "fail" if failed else "ok"})
    if res.matched_count == 0:
        raise HTTPException(404, "pond not found")
    return {"pond_id": pond_id, "pump_failed": failed}


@app.get("/incidents")
def list_incidents(state: IncidentState | None = None):
    q = {}
    if state:
        q["state"] = state.value
    return list(incidents_collection().find(q).sort("created_at", -1).limit(50))


@app.get("/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: str):
    rows = list(incidents_collection().aggregate(incident_timeline_pipeline(incident_id)))
    if not rows:
        raise HTTPException(404, "incident not found")
    set_span_tags(pond_id=rows[0].get("pond_id"))
    return rows[0]


@app.patch("/incidents/{incident_id}")
def patch_incident(incident_id: str, state: IncidentState, action: str | None = None):
    set_fields: dict = {"updated_at": datetime.utcnow(), "state": state.value}
    inc = incidents_collection().find_one({"_id": incident_id})
    if not inc:
        raise HTTPException(404, "incident not found")
    pond_id = inc.get("pond_id")
    set_span_tags(pond_id=pond_id)
    update_doc: dict = {"$set": set_fields}
    if action:
        events_collection().insert_one(
            {
                "timestamp": datetime.utcnow(),
                "pond_id": pond_id,
                "event": "incident_action",
                "incident_id": incident_id,
                "details": {"state": state.value, "action": action},
            }
        )
        update_doc["$push"] = {"actions": {"time": datetime.utcnow(), "state": state.value, "action": action}}
    incidents_collection().update_one({"_id": incident_id}, update_doc)
    return {"ok": True, "incident_id": incident_id, "state": state.value}
