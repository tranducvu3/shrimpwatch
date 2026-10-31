from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import BatchIngestRequest, ManualInput
from app.core.clock import utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import init_metrics
from app.db.mongo import ping
from app.db.schema import init_collections
from app.domain.models import IncidentState, MetricType
from app.services import incidents as incident_service
from app.services import ingest as ingest_service
from app.services import ponds as pond_service

logger = get_logger("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ShrimpWatch Water Quality Ingest API",
        version="1.0.0",
        description="Ingests sensor/manual/derived metrics for 6 shrimp ponds, emits Datadog metrics & traces.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    return app


app = create_app()


@app.on_event("startup")
def _startup():
    init_metrics()
    try:
        init_collections()
    except Exception as e:
        logger.warning("init_collections skipped (expected if DB not ready yet)", extra={"err": str(e)})


@app.get("/healthz")
def healthz():
    try:
        ping()
        mongo_ok = True
    except Exception as e:
        mongo_ok = False
        logger.error("Mongo ping failed", extra={"err": str(e)})
    return {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": mongo_ok,
        "farm_id": settings.FARM_ID,
        "env": settings.DD_ENV,
        "timestamp": utcnow().isoformat(),
    }


@app.post("/ingest/batch", status_code=202)
def ingest_batch(body: BatchIngestRequest) -> dict:
    try:
        return ingest_service.ingest_records(body.records)
    except ingest_service.IngestError as e:
        raise HTTPException(500, detail=str(e)) from e


@app.post("/ingest/manual/{metric}", status_code=202)
def ingest_manual(metric: MetricType, body: ManualInput) -> dict:
    try:
        return ingest_service.ingest_manual(
            metric, body.farm_id, body.pond_id, body.value, body.timestamp, body.tester_id
        )
    except ingest_service.NotAManualMetric as e:
        raise HTTPException(422, detail=str(e)) from e
    except ingest_service.IngestError as e:
        raise HTTPException(500, detail=str(e)) from e


@app.get("/ponds")
def list_ponds():
    return pond_service.list_ponds()


@app.post("/ponds/{pond_id}/pump/fail")
def mark_pump_failed(pond_id: str, failed: bool = True):
    try:
        pond_service.set_pump_failed(pond_id, failed)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "pump_failed": failed}


@app.get("/incidents")
def list_incidents(state: IncidentState | None = None):
    return incident_service.list_incidents(state)


@app.get("/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: str):
    try:
        return incident_service.timeline(incident_id)
    except incident_service.IncidentNotFound as e:
        raise HTTPException(404, "incident not found") from e


@app.patch("/incidents/{incident_id}")
def patch_incident(incident_id: str, state: IncidentState, action: str | None = None):
    try:
        incident_service.transition(incident_id, state, action)
    except incident_service.IncidentNotFound as e:
        raise HTTPException(404, "incident not found") from e
    return {"ok": True, "incident_id": incident_id, "state": state.value}
