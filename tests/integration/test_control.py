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
    # Offset over the limit -> 422 from domain
    r = client.post(f"/ponds/{POND}/scenarios", json={"scenario": "ph", "offset": 3.0})
    assert r.status_code == 422
    # Unknown scenario type / duration over the limit -> 422 from Pydantic
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


def test_live_readings_window(client: TestClient):
    doc = {
        "timestamp": utcnow() - timedelta(minutes=30),
        "farm_id": settings.FARM_ID,
        "pond_id": "pond-06",
        "device_id": "node-pond-06",
        "device_type": "water_node",
        "metric": "orp",
        "source": "sensor",
        "unit": "mV",
        "value": 123.0,
    }
    metrics_collection().insert_one(pack_ts_doc(doc))
    # Default 10 minutes: a record from 30 minutes ago is hidden; the simulator's 6h lookback sees it
    assert "orp" not in client.get("/ponds/readings").json().get("pond-06", {})
    assert client.get("/ponds/readings", params={"window_min": 60}).json()["pond-06"]["orp"]["value"] == 123.0
    assert client.get("/ponds/readings", params={"window_min": 100000}).status_code == 422


def test_manual_readings_look_back_a_day(client: TestClient):
    def enter(metric: str, hours_ago: float, value: float):
        body = {
            "timestamp": (utcnow() - timedelta(hours=hours_ago)).isoformat(),
            "farm_id": settings.FARM_ID,
            "pond_id": POND,
            "value": value,
        }
        assert client.post(f"/ingest/manual/{metric}", json=body).status_code == 202

    enter("alkalinity", 30, 90.0)  # older than 24 h: hidden
    enter("tan", 5, 0.4)
    enter("tan", 3, 0.7)
    enter("nitrite", 3, 0.3)

    manual = client.get("/ponds/manual-readings").json()[POND]
    assert set(manual) == {"tan", "nitrite"}
    assert manual["tan"]["value"] == 0.7
    assert manual["nitrite"]["unit"] == "mg/L"
    # Hours-old test-kit entries stay out of the sensor readings the simulator uses to find its backfill start
    assert not {"tan", "nitrite"} & set(client.get("/ponds/readings", params={"window_min": 360}).json().get(POND, {}))


def test_conditions_start_stack_and_clear(client: TestClient):
    for condition in ("overfeeding", "high_density"):
        r = client.post(f"/ponds/{POND}/conditions/{condition}")
        assert r.status_code == 200, r.text
    r = client.post(f"/ponds/{POND}/conditions/cloudy")
    assert r.status_code == 200 and "until" not in r.json()  # stays on until cleared

    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert set(pond["conditions"]) == {"overfeeding", "high_density", "cloudy"}
    changes = {c["metric"]: c for c in pond["changes"]}
    assert changes["do"]["offset"] == -4.5
    assert changes["tan"]["direction"] == "up"
    assert events_collection().find_one({"event": "condition_start", "pond_id": POND}) is not None

    assert client.delete(f"/ponds/{POND}/conditions/cloudy").status_code == 200
    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert set(pond["conditions"]) == {"overfeeding", "high_density"}

    assert client.delete(f"/ponds/{POND}/conditions").status_code == 200
    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert "conditions" not in pond and pond["changes"] == []
    assert events_collection().find_one({"event": "condition_clear", "pond_id": POND}) is not None


def test_conditions_reject_unknown_values(client: TestClient):
    assert client.post(f"/ponds/{POND}/conditions/volcano").status_code == 422
    assert client.post(f"/ponds/{POND}/conditions/$where").status_code == 422
    assert client.post("/ponds/pond-99/conditions/hot_sun").status_code == 404
    assert client.delete("/ponds/pond-99/conditions").status_code == 404


def test_scenario_catalog_and_control_page(client: TestClient):
    catalog = client.get("/ponds/scenarios/catalog").json()
    assert {s["type"] for s in catalog["scenarios"]} == {"do", "temperature", "ph", "salinity"}
    assert catalog["thresholds"]["do_critical"] == settings.DO_CRITICAL
    assert catalog["thresholds"]["tan_warn"] == settings.TAN_WARN
    assert len(catalog["conditions"]) == 13
    # A silent probe changes no reading of the water: it only stops them
    assert all(c["changes"] for c in catalog["conditions"] if c["type"] != "sensor_offline")
    r = client.get("/control")
    assert r.status_code == 200
    assert "ShrimpWatch" in r.text


def test_condition_severity_remedy_and_expiry(client: TestClient):
    r = client.post(f"/ponds/{POND}/conditions/dirty_bottom", params={"severity": "severe"})
    assert r.status_code == 200 and r.json()["severity"] == "severe"
    assert client.post(f"/ponds/{POND}/conditions/aerator_failure").status_code == 200
    assert client.post(f"/ponds/{POND}/conditions/dirty_bottom", params={"severity": "bogus"}).status_code == 422

    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert {a["type"]: a["severity"] for a in pond["active"]} == {"dirty_bottom": "severe", "aerator_failure": "medium"}
    assert pond["remedies"] == ["repair_aerator", "clean_bottom"]
    # The panel shows the destination (0.9 * 1.5), not the current ramp step
    assert {c["metric"]: c for c in pond["changes"]}["tan"]["offset"] == 1.35

    r = client.post(f"/ponds/{POND}/remedies/repair_aerator")
    assert r.status_code == 200 and r.json()["cleared"] == ["aerator_failure"]
    assert events_collection().find_one({"event": "remedy_applied", "pond_id": POND}) is not None
    assert client.post(f"/ponds/{POND}/remedies/volcano").status_code == 422
    assert client.post("/ponds/nope/remedies/cut_feed").status_code == 404

    # A one-off action that has run its course is no longer listed
    old = utcnow() - timedelta(days=1)
    ponds_collection().update_one({"_id": POND}, {"$set": {"conditions.liming": {"started_at": old}}})
    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert "liming" not in pond["conditions"]
    client.delete(f"/ponds/{POND}/conditions")


def test_catalog_lists_severities_and_remedies(client: TestClient):
    catalog = client.get("/ponds/scenarios/catalog").json()
    assert [s["type"] for s in catalog["severities"]] == ["light", "medium", "severe"]
    assert {r["type"] for r in catalog["remedies"]} >= {"repair_aerator", "clean_bottom"}
    liming = next(c for c in catalog["conditions"] if c["type"] == "liming")
    assert liming["duration_min"] and liming["ramp_min"] == 0


def test_starting_a_condition_ends_the_ones_it_contradicts(client: TestClient):
    for condition in ("heavy_rain", "overfeeding", "hot_sun"):
        assert client.post(f"/ponds/{POND}/conditions/{condition}").status_code == 200
    pond = next(p for p in client.get("/ponds").json() if p["_id"] == POND)
    assert set(pond["conditions"]) == {"overfeeding", "hot_sun"}  # hot sun replaced heavy rain, feeding untouched
    client.delete(f"/ponds/{POND}/conditions")
