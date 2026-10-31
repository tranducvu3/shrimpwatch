from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.clock import utcnow
from app.core.config import settings
from app.db.mongo import events_collection, metrics_collection, ponds_collection
from app.db.repositories.metrics import pack_ts_doc

POND = "pond-06"


def test_start_and_clear_scenario(client: TestClient):
    r = client.post(f"/ponds/{POND}/scenarios", json={"scenario": "temperature", "offset": 4.0, "duration_min": 20})
    assert r.status_code == 200, r.text
    assert r.json()["offset"] == 4.0

    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert pond["scenarios"]["temperature"]["offset"] == 4.0
    assert events_collection().find_one({"event": "scenario_start", "pond_id": POND}) is not None

    r = client.delete(f"/ponds/{POND}/scenarios/temperature")
    assert r.status_code == 200
    assert "temperature" not in ponds_collection().find_one({"_id": POND}).get("scenarios", {})
    assert events_collection().find_one({"event": "scenario_clear", "pond_id": POND}) is not None


def test_clear_all_scenarios(client: TestClient):
    for body in ({"scenario": "ph", "offset": 0.8}, {"scenario": "do", "offset": -3}):
        assert client.post(f"/ponds/{POND}/scenarios", json=body).status_code == 200
    assert client.delete(f"/ponds/{POND}/scenarios").status_code == 200
    assert "scenarios" not in ponds_collection().find_one({"_id": POND})


def test_scenario_validation(client: TestClient):
    # Vuot gioi han do lech -> 422 tu domain
    r = client.post(f"/ponds/{POND}/scenarios", json={"scenario": "ph", "offset": 3.0})
    assert r.status_code == 422
    # Loai kich ban khong ton tai / thoi gian vuot muc -> 422 tu Pydantic
    assert client.post(f"/ponds/{POND}/scenarios", json={"scenario": "$where", "offset": 1}).status_code == 422
    r = client.post(f"/ponds/{POND}/scenarios", json={"scenario": "ph", "offset": 0.1, "duration_min": 100000})
    assert r.status_code == 422
    assert client.delete(f"/ponds/{POND}/scenarios/wind").status_code == 422


def test_scenario_unknown_pond(client: TestClient):
    r = client.post("/ponds/pond-99/scenarios", json={"scenario": "ph", "offset": 0.5})
    assert r.status_code == 404
    assert client.delete("/ponds/pond-99/scenarios").status_code == 404


def test_live_readings_return_latest_value(client: TestClient):
    now = utcnow()
    for offset_sec, value in ((60, 5.1), (5, 4.2)):
        doc = {
            "timestamp": now - timedelta(seconds=offset_sec),
            "farm_id": settings.FARM_ID,
            "pond_id": POND,
            "device_id": f"node-{POND}",
            "device_type": "water_node",
            "metric": "do",
            "source": "sensor",
            "unit": "mg/L",
            "value": value,
        }
        metrics_collection().insert_one(pack_ts_doc(doc))
    r = client.get("/ponds/readings")
    assert r.status_code == 200
    assert r.json()[POND]["do"]["value"] == 4.2
    assert r.json()[POND]["do"]["unit"] == "mg/L"


def test_scenario_catalog_and_control_page(client: TestClient):
    catalog = client.get("/ponds/scenarios/catalog").json()
    assert {s["type"] for s in catalog["scenarios"]} == {"do", "temperature", "ph", "salinity"}
    assert catalog["thresholds"]["do_critical"] == settings.DO_CRITICAL
    r = client.get("/control")
    assert r.status_code == 200
    assert "ShrimpWatch" in r.text
