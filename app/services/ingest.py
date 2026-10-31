"""Nhan ban ghi telemetry (sensor/manual), luu vao Time Series va day gauge len Datadog."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.clock import to_naive_utc, utcnow
from app.core.log import get_logger
from app.core.telemetry import gauge, increment, set_span_tags
from app.db.repositories import metrics as metrics_repo
from app.domain.models import MANUAL_METRIC_UNITS, DeviceType, MetricRecord, MetricType, Source

logger = get_logger("services.ingest")


class IngestError(RuntimeError):
    pass


class NotAManualMetric(ValueError):
    pass


def ingest_records(records: list[MetricRecord]) -> dict:
    increment("ingest.batches", tags={"batch_size": len(records)})
    docs = []
    for r in records:
        docs.append(metrics_repo.record_to_doc(r))
        set_span_tags(pond_id=r.pond_id, device_type=r.device_type.value, metric=r.metric.value)
        gauge(
            f"metric.{r.metric.value}",
            r.value,
            tags={"pond": r.pond_id, "device_type": r.device_type.value, "source": r.source.value},
        )

    try:
        metrics_repo.insert_many(docs)
    except Exception as e:
        logger.error("ingest insert_many failed", extra={"err": str(e)})
        raise IngestError(f"mongo insert failed: {e}") from e

    # Du lieu tre/backfill nam ngoai cua so rollup 1 gio cua worker -> tu rollup cac gio no cham toi.
    timestamps = [to_naive_utc(r.timestamp) for r in records]
    if min(timestamps) < utcnow() - timedelta(hours=1):
        metrics_repo.run_hourly_rollup(min(timestamps), max(timestamps))

    ponds = sorted({r.pond_id for r in records})
    increment("ingest.records", value=len(docs))
    logger.info(
        "ingested batch",
        extra={"count": len(docs), "ponds": ponds, "metrics": sorted({r.metric.value for r in records})},
    )
    return {"ingested": len(docs), "ponds": ponds}


def ingest_manual(
    metric: MetricType, farm_id: str, pond_id: str, value: float, timestamp: datetime, tester_id: str
) -> dict:
    """Ket qua test-kit cua ky thuat vien (do kiem, TAN, nitrite)."""
    if metric not in MANUAL_METRIC_UNITS:
        raise NotAManualMetric(f"metric must be one of {[m.value for m in MANUAL_METRIC_UNITS]}")
    set_span_tags(pond_id=pond_id, device_type=DeviceType.MANUAL.value, metric=metric.value)
    record = MetricRecord(
        timestamp=timestamp,
        farm_id=farm_id,
        pond_id=pond_id,
        device_id=tester_id,
        device_type=DeviceType.MANUAL,
        metric=metric,
        value=value,
        source=Source.MANUAL,
        unit=MANUAL_METRIC_UNITS[metric],
    )
    return ingest_records([record])
