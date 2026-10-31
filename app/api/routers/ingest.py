from fastapi import APIRouter, HTTPException

from app.api.schemas import BatchIngestRequest, ManualInput
from app.domain.models import MetricType
from app.services import ingest as ingest_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/batch", status_code=202)
def ingest_batch(body: BatchIngestRequest) -> dict:
    try:
        return ingest_service.ingest_records(body.records)
    except ingest_service.IngestError as e:
        raise HTTPException(500, detail=str(e)) from e


@router.post("/manual/{metric}", status_code=202)
def ingest_manual(metric: MetricType, body: ManualInput) -> dict:
    """Ket qua test-kit: alkalinity, tan, nitrite."""
    try:
        return ingest_service.ingest_manual(
            metric, body.farm_id, body.pond_id, body.value, body.timestamp, body.tester_id
        )
    except ingest_service.NotAManualMetric as e:
        raise HTTPException(422, detail=str(e)) from e
    except ingest_service.IngestError as e:
        raise HTTPException(500, detail=str(e)) from e
