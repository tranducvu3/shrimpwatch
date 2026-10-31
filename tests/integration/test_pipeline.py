from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.clock import utcnow
from app.api.schemas import BatchIngestRequest, ManualInput
from app.core.config import settings
from app.db.mongo import (
    events_collection,
    get_client,
    incidents_collection,
    metrics_collection,
    metrics_hourly_collection,
    ponds_collection,
)
from app.db.pipelines import compute_do_drop_rate_pipeline, compute_ph_swing_pipeline
from app.db.repositories.metrics import pack_ts_doc, run_hourly_rollup
from app.db.schema import METRICS_POND_METRIC_TS_INDEX
from app.domain.models import DeviceType, MetricRecord, MetricType, Source
from app.domain.ponds import pond_ids


def _insert_record(
    metric: str,
    pond: str,
    value: float,
    ts: datetime | None = None,
    source: str = "sensor",
    device_type: str = "water_node",
):
    doc = pack_ts_doc(
        {
            "timestamp": ts or utcnow(),
            "farm_id": settings.FARM_ID,
            "pond_id": pond,
            "device_id": f"dev-{pond}",
            "device_type": device_type,
            "metric": metric,
            "source": source,
            "unit": "",
            "value": value,
        }
    )
    metrics_collection().insert_one(doc)


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["farm_id"] == settings.FARM_ID


def test_ponds_collection_seeded():
    ponds = list(ponds_collection().find())
    assert len(ponds) == settings.NUM_PONDS
    assert all(p["_id"].startswith("pond-") for p in ponds)


def test_timeseries_collection_metadata():
    db = get_client()[settings.MONGODB_DB]
    try:
        info_cursor = db.command("listCollections", 1, filter={"name": "metrics"})
    except Exception:
        # Fallback: dung admin DB voi $listCatalog neu user co quyen
        adm = get_client()["admin"]
        infos = list(adm.aggregate([{"$listCatalog": {}}]))
        ts_info = next(
            (
                i
                for i in infos
                if i.get("name") == "metrics" and (i.get("db") == settings.MONGODB_DB or i.get("md", {}).get("options"))
            ),
            None,
        )
        assert ts_info is not None, "metrics collection not found via admin listCatalog"
        md = ts_info.get("md") or {}
        assert md.get("timeseries") is not None, "metrics must be a Time Series collection (Yêu cầu #1)"
        opts = md.get("options") or {}
        expire = opts.get("expireAfterSeconds")
        assert expire is not None, "must have TTL expireAfterSeconds (Yêu cầu #1)"
        return

    cursor = info_cursor.get("cursor", {})
    batch = cursor.get("firstBatch", cursor.get("nextBatch", []))
    assert len(batch) >= 1, "metrics collection not found via listCollections"
    entry = batch[0]
    opts = entry.get("options", {})
    assert opts.get("timeseries") is not None, "metrics must be a Time Series collection (Yêu cầu #1)"
    assert "expireAfterSeconds" in opts, "must have TTL expireAfterSeconds (Yêu cầu #1)"
    expire = opts["expireAfterSeconds"]
    assert isinstance(expire, int) and expire > 0


def test_ingest_batch_and_ttl_schema(client: TestClient):
    rec = MetricRecord(
        timestamp=utcnow(),
        farm_id=settings.FARM_ID,
        pond_id="pond-01",
        device_id="node-pond-01",
        device_type=DeviceType.WATER_NODE,
        metric=MetricType.DO,
        value=5.2,
        source=Source.SENSOR,
        unit="mg/L",
    )
    req = BatchIngestRequest(records=[rec])
    r = client.post("/ingest/batch", json=req.model_dump(mode="json"))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ingested"] == 1

    stored = metrics_collection().find_one(
        {"metadata.pond_id": "pond-01", "metadata.metric": "do"},
        sort=[("timestamp", -1)],
    )
    assert stored is not None
    assert stored["value"] == 5.2
    assert stored["metadata"]["source"] == "sensor"
    assert stored["metadata"]["pond_id"] == "pond-01"


def test_manual_alkalinity_ingest(client: TestClient):
    body = ManualInput(
        farm_id=settings.FARM_ID,
        pond_id="pond-02",
        value=168.0,
        tester_id="tech-002",
    )
    r = client.post("/ingest/manual/alkalinity", json=body.model_dump(mode="json"))
    assert r.status_code == 202
    stored = metrics_collection().find_one(
        {"metadata.pond_id": "pond-02", "metadata.metric": "alkalinity", "metadata.source": "manual"},
        sort=[("timestamp", -1)],
    )
    assert stored is not None
    assert stored["value"] == 168.0


def test_manual_tan_and_nitrite_ingest(client: TestClient):
    for metric, value in (("tan", 0.8), ("nitrite", 0.4)):
        r = client.post(
            f"/ingest/manual/{metric}",
            json={"farm_id": settings.FARM_ID, "pond_id": "pond-02", "value": value},
        )
        assert r.status_code == 202, r.text
        stored = metrics_collection().find_one(
            {"metadata.pond_id": "pond-02", "metadata.metric": metric, "metadata.source": "manual"},
            sort=[("timestamp", -1)],
        )
        assert stored is not None
        assert stored["value"] == value


def test_manual_ingest_rejects_sensor_metric(client: TestClient):
    r = client.post(
        "/ingest/manual/do",
        json={"farm_id": settings.FARM_ID, "pond_id": "pond-02", "value": 5.0},
    )
    assert r.status_code == 422


def test_manual_ingest_rejects_negative_value(client: TestClient):
    r = client.post(
        "/ingest/manual/tan",
        json={"farm_id": settings.FARM_ID, "pond_id": "pond-02", "value": -5},
    )
    assert r.status_code == 422


def test_setwindowfields_do_drop_rate_pipeline():
    now = utcnow()
    start = now - timedelta(minutes=10)
    for i, t in enumerate([start + timedelta(seconds=30 * j) for j in range(20)]):
        _insert_record("do", "pond-03", max(1.5, 7.0 - i * 0.15), ts=t)
    pipeline = compute_do_drop_rate_pipeline(["pond-03"], window_sec=300)
    rows = list(metrics_collection().aggregate(pipeline))
    assert len(rows) == 1, "expected exactly one row per pond"
    row = rows[0]
    assert row["pond_id"] == "pond-03"
    assert row["do_drop_mg_per_l"] > 0, "DO is dropping so drop_mg_per_l should be > 0"
    # 0.15 mg/L moi 30s = 0.3 mg/L/phut
    assert row["do_drop_rate_mg_per_l_per_min"] == pytest.approx(0.3, abs=0.01)
    # This result is used as Yêu cầu #2 evidence stored in incidents.evidence
    assert "min_do" in row


def test_co2_derivation_formula():
    from app.services.derivations import derive_co2_for_pond

    now = utcnow()
    _insert_record("alkalinity", "pond-04", 170.0, ts=now, source="manual", device_type="manual_tester")
    _insert_record("ph", "pond-04", 7.9, ts=now)
    co2 = derive_co2_for_pond("pond-04", now)
    assert co2 is not None
    # Kiểm tra thử: pH 7.9, alk 170 => khoảng 4.2 mg/L (theo đề bài)
    assert 3.5 <= co2 <= 5.0, f"Expected CO2 ~4.2, got {co2}"


def test_pump_fail_event_and_incident_opening(client: TestClient):
    from app.worker.pond_check import check_pond

    now = utcnow()
    for i in range(10):
        _insert_record(
            "pump_power",
            "pond-05",
            10.0 if i >= 5 else 1500.0,
            ts=now - timedelta(seconds=30 * (10 - i)),
            device_type="pump",
        )
    for i in range(15):
        _insert_record(
            "do",
            "pond-05",
            max(2.0, 6.0 - i * 0.15),
            ts=now - timedelta(seconds=30 * (15 - i)),
        )

    r = client.post("/ponds/pond-05/pump/fail", params={"failed": "true"})
    assert r.status_code == 200
    pond = ponds_collection().find_one({"_id": "pond-05"})
    assert pond["pump"]["failed"] is True

    check_pond("pond-05")
    inc = incidents_collection().find_one({"pond_id": "pond-05"})
    assert inc is not None
    assert inc["severity"] in ("critical", "warning")
    assert "evidence" in inc
    # Yêu cầu #2: kết quả $setWindowFields nằm trong incidents.evidence
    assert "do_drop_rate_mg_per_l_per_min" in inc["evidence"] or "avg_pump_power_watts" in inc["evidence"]
    assert inc.get("root_cause") == "pump_failure" or inc.get("title")


def test_incident_lifecycle_patch(client: TestClient):
    incidents_collection().insert_one(
        {
            "_id": "inc-test-001",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "farm_id": settings.FARM_ID,
            "pond_id": "pond-06",
            "severity": "critical",
            "state": "open",
            "title": "test incident",
            "description": "test",
            "evidence": {},
            "actions": [],
        }
    )
    r = client.patch(
        "/incidents/inc-test-001",
        params={"state": "investigating", "action": "Điều tra quạt"},
    )
    assert r.status_code == 200
    r = client.patch(
        "/incidents/inc-test-001",
        params={"state": "resolving", "action": "Sửa quạt"},
    )
    assert r.status_code == 200
    r = client.patch(
        "/incidents/inc-test-001",
        params={"state": "recovered", "action": "AO ổn định DO"},
    )
    assert r.status_code == 200

    r = client.get("/incidents", params={"state": "recovered"})
    assert r.status_code == 200
    found = any(x["_id"] == "inc-test-001" for x in r.json())
    assert found, "Expected incident in recovered state"


def test_nh3_derivation_formula():
    from app.services.derivations import derive_nh3_for_pond

    now = utcnow()
    _insert_record("tan", "pond-01", 1.0, ts=now, source="manual", device_type="manual_tester")
    _insert_record("ph", "pond-01", 8.0, ts=now)
    _insert_record("temperature", "pond-01", 30.0, ts=now)
    nh3 = derive_nh3_for_pond("pond-01", now)
    assert nh3 is not None
    # pH 8.0, 30 do C: ~7-8% TAN o dang NH3 doc
    assert 0.06 <= nh3 <= 0.09, f"Expected NH3 ~0.075, got {nh3}"


def test_ph_swing_pipeline():
    now = utcnow()
    for i, v in enumerate([7.8, 8.4, 8.1, 7.9]):
        _insert_record("ph", "pond-06", v, ts=now - timedelta(hours=i * 3))
    run_hourly_rollup(now - timedelta(hours=24))
    rows = list(metrics_hourly_collection().aggregate(compute_ph_swing_pipeline("pond-06")))
    assert len(rows) == 1
    assert rows[0]["ph_swing"] == 0.6


def test_do_drop_rate_survives_sensor_dropout():
    # Cam bien rot goi: chi con 3 diem trong 5 phut. Cua so theo thoi gian van cho dung toc do.
    now = utcnow()
    for minutes_ago, value in ((4, 6.0), (2, 5.4), (0, 4.8)):
        _insert_record("do", "pond-02", value, ts=now - timedelta(minutes=minutes_ago))
    rows = list(metrics_collection().aggregate(compute_do_drop_rate_pipeline(["pond-02"], window_sec=300)))
    assert rows[0]["do_drop_rate_mg_per_l_per_min"] == pytest.approx(0.3, abs=0.01)
    assert rows[0]["min_do"] == 4.8


def test_do_drop_rate_all_ponds_in_one_query():
    ponds = pond_ids()
    now = utcnow()
    for pond in ponds:
        _insert_record("do", pond, 6.0, ts=now - timedelta(seconds=1))
    rows = list(metrics_collection().aggregate(compute_do_drop_rate_pipeline(ponds, window_sec=300)))
    assert sorted(r["pond_id"] for r in rows) == ponds


def test_hourly_rollup_is_idempotent():
    hour = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    for i, v in enumerate([20.0, 30.0, 25.0]):
        _insert_record("salinity", "pond-04", v, ts=hour + timedelta(minutes=10 * i))
    run_hourly_rollup(hour)
    run_hourly_rollup(hour)
    rows = list(metrics_hourly_collection().find({"pond_id": "pond-04", "metric": "salinity", "hour": hour}))
    assert len(rows) == 1
    assert (rows[0]["min"], rows[0]["max"], rows[0]["avg"], rows[0]["count"]) == (20.0, 30.0, 25.0, 3)


def test_worker_queries_use_compound_index():
    plan = (
        metrics_collection()
        .find(
            {
                "metadata.pond_id": "pond-01",
                "metadata.metric": "do",
                "timestamp": {"$gte": utcnow() - timedelta(minutes=5)},
            }
        )
        .sort("timestamp", -1)
        .explain()
    )
    assert f"'indexName': '{METRICS_POND_METRIC_TS_INDEX}'" in str(plan)


def test_incident_timeline_joins_events(client: TestClient):
    incidents_collection().insert_one(
        {
            "_id": "inc-timeline-001",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "farm_id": settings.FARM_ID,
            "pond_id": "pond-03",
            "severity": "critical",
            "state": "open",
            "title": "timeline test",
            "description": "test",
            "evidence": {},
            "actions": [],
        }
    )
    assert client.post("/ponds/pond-03/pump/fail", params={"failed": "true"}).status_code == 200
    client.patch("/incidents/inc-timeline-001", params={"state": "investigating", "action": "Kiem tra quat"})

    action = events_collection().find_one({"event": "incident_action", "incident_id": "inc-timeline-001"})
    assert action is not None, "incident_id must be top-level on every event"

    r = client.get("/incidents/inc-timeline-001/timeline")
    assert r.status_code == 200, r.text
    # Bo qua goi y Vector Search: co hay khong tuy lich su su co do test khac de lai
    events = [e["event"] for e in r.json()["timeline"] if e["event"] != "incident_similar_matched"]
    assert events == ["pump_state_change", "incident_action"]
    assert client.get("/incidents/inc-missing/timeline").status_code == 404


def test_backfill_ingest_rolls_up_old_hours(client: TestClient):
    # Simulator nap 6h lich su khi khoi dong; worker chi tinh lai gio gan nhat -> API phai tu rollup.
    old = utcnow() - timedelta(hours=3)
    hour = old.replace(minute=0, second=0, microsecond=0)
    records = [
        {
            "timestamp": ts,
            "farm_id": settings.FARM_ID,
            "pond_id": "pond-06",
            "device_id": "node-pond-06",
            "device_type": "water_node",
            "metric": "orp",
            "value": value,
            "source": "sensor",
            "unit": "mV",
        }
        for ts, value in ((old.isoformat(), 250.0), (old.isoformat() + "Z", 150.0))
    ]
    r = client.post("/ingest/batch", json={"records": records})
    assert r.status_code == 202, r.text

    row = metrics_hourly_collection().find_one({"pond_id": "pond-06", "metric": "orp", "hour": hour})
    assert row is not None, "backfilled hour must be rolled up on ingest"
    assert (row["min"], row["max"], row["count"]) == (150.0, 250.0, 2)


def test_incident_patch_rejects_invalid_transition(client: TestClient):
    incidents_collection().insert_one(
        {
            "_id": "inc-done-001",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "farm_id": settings.FARM_ID,
            "pond_id": "pond-02",
            "severity": "warning",
            "state": "recovered",
            "title": "already recovered",
            "description": "test",
            "evidence": {},
            "actions": [],
        }
    )
    r = client.patch("/incidents/inc-done-001", params={"state": "open", "action": "reopen"})
    assert r.status_code == 409, r.text
    assert incidents_collection().find_one({"_id": "inc-done-001"})["state"] == "recovered"
    assert events_collection().find_one({"incident_id": "inc-done-001"}) is None
    assert client.patch("/incidents/inc-missing", params={"state": "investigating"}).status_code == 404


def test_pump_fail_unknown_pond_is_404_without_event(client: TestClient):
    r = client.post("/ponds/pond-99/pump/fail", params={"failed": "true"})
    assert r.status_code == 404
    assert events_collection().find_one({"pond_id": "pond-99"}) is None


def test_worker_auto_recovers_when_do_is_healthy():
    from app.worker.pond_check import check_pond

    incidents_collection().insert_one(
        {
            "_id": "inc-auto-001",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "farm_id": settings.FARM_ID,
            "pond_id": "pond-04",
            "severity": "warning",
            "state": "investigating",
            "title": "low DO",
            "description": "test",
            "evidence": {},
            "actions": [],
        }
    )
    check_pond("pond-04", {"do_drop_rate_mg_per_l_per_min": 0.0, "min_do": 6.5})
    inc = incidents_collection().find_one({"_id": "inc-auto-001"})
    assert inc["state"] == "recovered"
    assert inc["actions"][-1]["action"] == "worker_auto_recovered"
    assert events_collection().find_one({"event": "incident_recovered", "incident_id": "inc-auto-001"})
