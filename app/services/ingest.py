"""Accept telemetry records (sensor/manual), store them in the Time Series and send gauges to Datadog."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.clock import to_naive_utc, utcnow
from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, increment, set_span_tags
from app.db.repositories import metrics as metrics_repo
from app.domain.incident_rules import PondSnapshot, Thresholds, evaluate, pump_stopped
from app.domain.models import MANUAL_METRIC_UNITS, DeviceType, IncidentEvidence, MetricRecord, MetricType, Source
from app.services import incidents as incident_service

logger = get_logger("services.ingest")

# Pump readings this recent count as live; older ones are a backfill and must not open incidents
LIVE_READING_AGE = timedelta(minutes=2)
STOPPED_LOOKBACK = timedelta(minutes=5)


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

    # Late/backfilled data falls outside the worker's 1-hour rollup window -> roll up the hours it touches here.
    timestamps = [to_naive_utc(r.timestamp) for r in records]
    if min(timestamps) < utcnow() - timedelta(hours=1):
        metrics_repo.run_hourly_rollup(min(timestamps), max(timestamps))

    _check_stopped_aerators(records)

    ponds = sorted({r.pond_id for r in records})
    increment("ingest.records", value=len(docs))
    logger.info(
        "ingested batch",
        extra={"count": len(docs), "ponds": ponds, "metrics": sorted({r.metric.value for r in records})},
    )
    return {"ingested": len(docs), "ponds": ponds}


def _check_stopped_aerators(records: list[MetricRecord]) -> None:
    """Open a critical incident as soon as a pond's aerator reports "off" twice in a row, without waiting for the
    worker's next cycle and 5-minute averages. A failed check is logged and never fails the ingest."""
    now = utcnow()
    stopped_now = {
        r.pond_id
        for r in records
        if r.metric == MetricType.PUMP_STATUS and r.value == 0 and now - to_naive_utc(r.timestamp) <= LIVE_READING_AGE
    }
    for pond_id in sorted(stopped_now):
        try:
            _open_if_stopped(pond_id, now)
        except Exception as e:
            logger.error("stopped aerator check failed", extra={"pond": pond_id, "err": str(e)})


def _open_if_stopped(pond_id: str, now: datetime) -> None:
    required = settings.PUMP_STOPPED_READINGS
    statuses = metrics_repo.latest_values(pond_id, MetricType.PUMP_STATUS.value, now - STOPPED_LOOKBACK, required)
    if not pump_stopped(statuses, required):
        return
    snapshot = PondSnapshot(pond_id=pond_id, avg_pump_watts=None, do_drop_rate=None, min_do=None, pump_stopped=True)
    finding = evaluate(snapshot, Thresholds.from_settings())
    if finding is None:  # cannot happen for a stopped aerator; keeps the type checker honest
        return
    evidence = IncidentEvidence(notes={"timestamp": now.isoformat(), "detected_by": "ingest", "pump_status": statuses})
    incident_id = incident_service.open_or_escalate(pond_id, finding, evidence)
    if incident_id:
        logger.warning("stopped aerator detected at ingest", extra={"pond": pond_id, "incident_id": incident_id})


def ingest_manual(
    metric: MetricType, farm_id: str, pond_id: str, value: float, timestamp: datetime, tester_id: str
) -> dict:
    """A technician's test-kit results (alkalinity, TAN, nitrite)."""
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
