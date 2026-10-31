from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta

from ddtrace import tracer

from app.config import settings
from app.db import (
    compute_do_drop_rate_pipeline,
    compute_ph_swing_pipeline,
    events_collection,
    incidents_collection,
    metrics_collection,
    metrics_hourly_collection,
    pack_ts_doc,
    run_hourly_rollup,
)
from app.logging_setup import get_logger
from app.metrics import gauge, increment, set_span_tags, init_metrics
from app.models import (
    DeviceType,
    Incident,
    IncidentEvidence,
    IncidentState,
    Severity,
    Source,
)

logger = get_logger("worker")


def derive_co2_for_pond(pond_id: str, timestamp: datetime) -> float | None:
    last_alk_doc = metrics_collection().find_one(
        {
            "metadata.pond_id": pond_id,
            "metadata.metric": "alkalinity",
            "metadata.source": {"$in": ["manual"]},
            "timestamp": {"$gte": timestamp - timedelta(hours=24)},
        },
        sort=[("timestamp", -1)],
    )
    last_ph_doc = metrics_collection().find_one(
        {
            "metadata.pond_id": pond_id,
            "metadata.metric": "ph",
            "metadata.source": {"$in": ["sensor"]},
            "timestamp": {"$gte": timestamp - timedelta(minutes=30)},
        },
        sort=[("timestamp", -1)],
    )
    if not last_alk_doc or not last_ph_doc:
        return None
    alk = last_alk_doc["value"]
    ph = last_ph_doc["value"]
    co2 = 44000.0 * (alk / 50000.0) * (10 ** (6.35 - ph))
    return round(co2, 3)


def _latest_value(pond_id: str, metric: str, since: datetime) -> float | None:
    doc = metrics_collection().find_one(
        {"metadata.pond_id": pond_id, "metadata.metric": metric, "timestamp": {"$gte": since}},
        sort=[("timestamp", -1)],
    )
    return doc["value"] if doc else None


def nh3_fraction(ph: float, temp_c: float) -> float:
    # Emerson et al. (1975): pKa = 0.09018 + 2729.92 / T(K)
    pka = 0.09018 + 2729.92 / (temp_c + 273.15)
    return 1.0 / (1.0 + 10 ** (pka - ph))


def derive_nh3_for_pond(pond_id: str, timestamp: datetime) -> float | None:
    tan = _latest_value(pond_id, "tan", timestamp - timedelta(hours=24))
    ph = _latest_value(pond_id, "ph", timestamp - timedelta(minutes=30))
    temp = _latest_value(pond_id, "temperature", timestamp - timedelta(minutes=30))
    if tan is None or ph is None or temp is None:
        return None
    return round(tan * nh3_fraction(ph, temp), 4)


def compute_ph_swing(pond_id: str) -> float | None:
    rows = list(metrics_hourly_collection().aggregate(compute_ph_swing_pipeline(pond_id)))
    return rows[0]["ph_swing"] if rows else None


def write_derived_record(pond_id: str, timestamp: datetime, metric: str, value: float, unit: str):
    doc = pack_ts_doc(
        {
            "timestamp": timestamp,
            "farm_id": settings.FARM_ID,
            "pond_id": pond_id,
            "device_id": f"worker-{metric}-{pond_id}",
            "device_type": DeviceType.WORKER.value,
            "metric": metric,
            "source": Source.DERIVED.value,
            "unit": unit,
            "value": value,
        }
    )
    metrics_collection().insert_one(doc)
    gauge(
        f"metric.{metric}",
        value,
        tags={"pond": pond_id, "device_type": DeviceType.WORKER.value, "source": Source.DERIVED.value},
    )
    increment(f"{metric}.derived", tags={"pond": pond_id})


def evaluate_do_drop(pond_ids: list[str], window_sec: int = 300) -> dict[str, dict]:
    pipeline = compute_do_drop_rate_pipeline(pond_ids, window_sec=window_sec)
    rows = {row["pond_id"]: row for row in metrics_collection().aggregate(pipeline)}
    for pond_id, row in rows.items():
        logger.info("DO window computed", extra={"pond": pond_id, "row": row})
    return rows


def avg_pump_power_last_n(pond_id: str, minutes: int = 5) -> float | None:
    docs = list(
        metrics_collection()
        .find(
            {
                "metadata.pond_id": pond_id,
                "metadata.metric": "pump_power",
                "timestamp": {"$gte": datetime.utcnow() - timedelta(minutes=minutes)},
            }
        )
        .sort("timestamp", -1)
        .limit(30)
    )
    if not docs:
        return None
    return sum(d["value"] for d in docs) / len(docs)


def find_open_incident(pond_id: str) -> dict | None:
    return incidents_collection().find_one(
        {"pond_id": pond_id, "state": {"$in": [IncidentState.OPEN.value, IncidentState.INVESTIGATING.value]}}
    )


def open_or_update_incident(
    pond_id: str,
    severity: Severity,
    title: str,
    description: str,
    evidence: IncidentEvidence,
    root_cause: str | None = None,
) -> str:
    existing = find_open_incident(pond_id)
    if existing:
        incidents_collection().update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "updated_at": datetime.utcnow(),
                    "severity": severity.value,
                    "title": title,
                    "description": description,
                    "evidence": evidence.model_dump(),
                    "root_cause": root_cause or existing.get("root_cause"),
                }
            },
        )
        return existing["_id"]

    incident_id = f"inc-{uuid.uuid4().hex[:10]}"
    inc = Incident(
        id=incident_id,
        farm_id=settings.FARM_ID,
        pond_id=pond_id,
        severity=severity,
        title=title,
        description=description,
        evidence=evidence,
        root_cause=root_cause,
    )
    incidents_collection().insert_one(inc.model_dump(by_alias=True))
    events_collection().insert_one(
        {
            "timestamp": datetime.utcnow(),
            "pond_id": pond_id,
            "event": "incident_open",
            "incident_id": incident_id,
            "details": {"severity": severity.value, "title": title},
        }
    )
    increment(
        "incident.open",
        tags={"pond": pond_id, "severity": severity.value, "root_cause": root_cause or "unknown"},
    )
    logger.error("incident opened", extra={"incident_id": incident_id, "pond": pond_id, "severity": severity.value})
    return incident_id


def check_pond(pond_id: str, do_row: dict | None = None):
    set_span_tags(pond_id=pond_id, device_type=DeviceType.WORKER.value)
    if do_row is None:
        do_row = evaluate_do_drop([pond_id]).get(pond_id)
    now = datetime.utcnow()

    co2 = derive_co2_for_pond(pond_id, now)
    if co2 is not None:
        if co2 > settings.CO2_WARN:
            logger.warning("CO2 warning", extra={"pond": pond_id, "co2": co2})
        write_derived_record(pond_id, now, "co2", co2, "mg/L")

    nh3 = derive_nh3_for_pond(pond_id, now)
    if nh3 is not None:
        if nh3 > settings.NH3_WARN:
            logger.warning("NH3 warning", extra={"pond": pond_id, "nh3": nh3})
        write_derived_record(pond_id, now, "nh3", nh3, "mg/L")

    ph_swing = compute_ph_swing(pond_id)
    if ph_swing is not None:
        if ph_swing > settings.PH_SWING_WARN:
            logger.warning("pH swing warning", extra={"pond": pond_id, "ph_swing": ph_swing})
        write_derived_record(pond_id, now, "ph_swing", ph_swing, "pH")

    last_orp = _latest_value(pond_id, "orp", now - timedelta(minutes=5))
    if last_orp is not None and last_orp < settings.ORP_WARN_LOW:
        logger.warning("ORP low: day ao thieu oxy, nguy co H2S", extra={"pond": pond_id, "orp": last_orp})

    avg_pump = avg_pump_power_last_n(pond_id, minutes=5)
    pump_failed = avg_pump is not None and avg_pump < 50.0

    drop_rate = do_row.get("do_drop_rate_mg_per_l_per_min") if do_row else None
    min_do = do_row.get("min_do") if do_row else None

    evidence = IncidentEvidence(
        do_drop_rate_mg_per_l_per_min=drop_rate,
        avg_pump_power_watts=avg_pump,
        min_do=min_do,
        last_orp_mv=last_orp,
        nh3_mg_per_l=nh3,
        ph_swing_24h=ph_swing,
        do_window_sec=300,
        notes={"timestamp": now.isoformat()},
    )

    severity = Severity.INFO
    title = None
    desc = None
    root_cause = None

    if pump_failed and (drop_rate is None or drop_rate >= 0):
        severity = Severity.CRITICAL
        title = "SỰ CỐ: Quạt nước hỏng → oxy sụt"
        desc = (
            f"Công suất quạt ao {pond_id} trung bình 5 phút gần nhất: {avg_pump:.1f} W. "
            f"DO drop rate: {drop_rate:.4f} mg/L/phút, min DO: {min_do}"
        )
        root_cause = "pump_failure"
    elif drop_rate is not None and drop_rate >= settings.DO_DROP_RATE_WARN_MGPERL_PER_MIN and (not pump_failed):
        severity = Severity.WARNING
        title = "Cảnh báo: Oxy giảm nhanh bất thường"
        desc = (
            f"Tốc độ giảm DO {drop_rate:.3f} mg/L/phút (> ngưỡng {settings.DO_DROP_RATE_WARN_MGPERL_PER_MIN}). "
            f"Cần điều tra."
        )
        root_cause = "do_anomaly_drop"
    elif min_do is not None and min_do < settings.DO_CRITICAL:
        severity = Severity.CRITICAL
        title = "CRITICAL: DO dưới ngưỡng nguy hiểm"
        desc = f"Min DO = {min_do:.3f} mg/L < {settings.DO_CRITICAL}"
        root_cause = "do_critical"
    elif min_do is not None and min_do < settings.DO_WARN:
        severity = Severity.WARNING
        title = "WARNING: DO dưới ngưỡng cảnh báo"
        desc = f"Min DO = {min_do:.3f} mg/L < {settings.DO_WARN}"
        root_cause = "do_low"

    if title:
        open_or_update_incident(pond_id, severity, title, desc, evidence, root_cause=root_cause)
    else:
        existing = find_open_incident(pond_id)
        if existing and min_do is not None and min_do > settings.DO_WARN + 0.5 and (avg_pump is None or avg_pump > 500):
            incidents_collection().update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "updated_at": datetime.utcnow(),
                        "state": IncidentState.RECOVERED.value,
                    },
                    "$push": {
                        "actions": {
                            "time": datetime.utcnow(),
                            "state": IncidentState.RECOVERED.value,
                            "action": "worker_auto_recovered",
                        }
                    },
                },
            )
            events_collection().insert_one(
                {
                    "timestamp": datetime.utcnow(),
                    "pond_id": pond_id,
                    "event": "incident_recovered",
                    "incident_id": existing["_id"],
                }
            )
            increment("incident.recovered", tags={"pond": pond_id})
            logger.info("incident auto-recovered", extra={"pond": pond_id, "incident_id": existing["_id"]})


def run_worker():
    init_metrics()
    logger.info("worker starting")
    ponds = [f"pond-{i:02d}" for i in range(1, settings.NUM_PONDS + 1)]
    # Lan dau: backfill toan bo khoang TTL; sau do chi tinh lai gio truoc + gio hien tai.
    rollup_since = datetime.utcnow() - timedelta(seconds=settings.TS_TTL_SECONDS)
    last_rollup = 0.0
    while True:
        t0 = time.time()
        if t0 - last_rollup >= settings.ROLLUP_INTERVAL_SEC:
            try:
                with tracer.trace("worker.rollup_hourly"):
                    run_hourly_rollup(rollup_since)
                last_rollup = t0
                rollup_since = datetime.utcnow() - timedelta(hours=1)
            except Exception as e:
                logger.error("hourly rollup failed", extra={"err": str(e)})
        try:
            with tracer.trace("worker.do_window"):
                do_rows = evaluate_do_drop(ponds)
        except Exception as e:
            logger.error("DO window failed", extra={"err": str(e)})
            do_rows = {}
        for pid in ponds:
            try:
                with tracer.trace("worker.check_pond", resource=pid):
                    check_pond(pid, do_rows.get(pid))
            except Exception as e:
                logger.error("check_pond failed", extra={"pond": pid, "err": str(e)})
        elapsed = time.time() - t0
        time.sleep(max(1.0, 30.0 - elapsed))


if __name__ == "__main__":
    run_worker()
