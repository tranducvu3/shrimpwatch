"""Doc/ghi telemetry trong Time Series `metrics` va rollup `metrics_hourly`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.mongo import metrics_collection, metrics_hourly_collection
from app.db.pipelines import compute_do_drop_rate_pipeline, compute_ph_swing_pipeline, rollup_hourly_pipeline
from app.domain.models import MetricRecord

_TS_META = ["farm_id", "pond_id", "device_id", "device_type", "metric", "source", "unit"]


def pack_ts_doc(rec: dict[str, Any]) -> dict[str, Any]:
    """Ban ghi phang -> document Time Series: cac truong mo ta nam trong `metadata`."""
    return {
        "timestamp": rec["timestamp"],
        "metadata": {k: rec.get(k) for k in _TS_META if k in rec},
        "value": rec["value"],
    }


def record_to_doc(r: MetricRecord) -> dict[str, Any]:
    return pack_ts_doc(
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


def insert_many(docs: list[dict[str, Any]]) -> None:
    if docs:
        metrics_collection().insert_many(docs, ordered=False)


def insert_one(doc: dict[str, Any]) -> None:
    metrics_collection().insert_one(doc)


def latest_value(pond_id: str, metric: str, since: datetime, source: str | None = None) -> float | None:
    query: dict[str, Any] = {"metadata.pond_id": pond_id, "metadata.metric": metric, "timestamp": {"$gte": since}}
    if source:
        query["metadata.source"] = source
    doc = metrics_collection().find_one(query, sort=[("timestamp", -1)])
    return doc["value"] if doc else None


def average_value(pond_id: str, metric: str, since: datetime, limit: int = 30) -> float | None:
    """Trung binh `limit` diem moi nhat ke tu `since`."""
    docs = list(
        metrics_collection()
        .find({"metadata.pond_id": pond_id, "metadata.metric": metric, "timestamp": {"$gte": since}})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return sum(d["value"] for d in docs) / len(docs) if docs else None


def do_windows(pond_ids: list[str], window_sec: int) -> dict[str, dict]:
    """Ket qua cua so DO 5 phut cho nhieu ao trong 1 lan aggregate, theo pond_id."""
    pipeline = compute_do_drop_rate_pipeline(pond_ids, window_sec=window_sec)
    return {row["pond_id"]: row for row in metrics_collection().aggregate(pipeline)}


def ph_swing(pond_id: str) -> float | None:
    rows = list(metrics_hourly_collection().aggregate(compute_ph_swing_pipeline(pond_id)))
    return rows[0]["ph_swing"] if rows else None


def run_hourly_rollup(since: datetime, until: datetime | None = None) -> None:
    metrics_collection().aggregate(rollup_hourly_pipeline(since, until))
