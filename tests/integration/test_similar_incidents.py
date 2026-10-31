import pytest
from fastapi.testclient import TestClient

from app.cli.seed_incident_history import build_history
from app.db.mongo import events_collection, incidents_collection
from app.domain.incident_fingerprint import fingerprint, similarity
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.services import incidents as incident_service

PUMP_EVIDENCE = IncidentEvidence(
    do_drop_rate_mg_per_l_per_min=0.1,
    avg_pump_power_watts=0.0,
    min_do=3.3,
    last_orp_mv=145.0,
    nh3_mg_per_l=0.05,
    ph_swing_24h=0.33,
)


@pytest.fixture
def history():
    # Chi xoa lich su tong hop (hist-*), khong dung toi du lieu cua test khac
    hist = {"$regex": "^hist-"}
    incidents_collection().delete_many({"_id": hist})
    events_collection().delete_many({"incident_id": hist})
    items = build_history()
    assert incident_service.import_history(items) == len(items)
    # Lan 2 khong nap trung
    assert incident_service.import_history(items) == 0
    return items


def _open_pump_incident(pond_id: str = "pond-05") -> str:
    finding = Finding(Severity.CRITICAL, "pump", "pump failed", "pump_failure")
    return incident_service.open_or_update(pond_id, finding, PUMP_EVIDENCE)


def test_open_incident_stores_fingerprint(history):
    incident_id = _open_pump_incident()
    doc = incidents_collection().find_one({"_id": incident_id})
    assert doc["fingerprint"] == fingerprint(PUMP_EVIDENCE)


def test_similar_incidents_ranks_pump_failures_first(history, client: TestClient):
    incident_id = _open_pump_incident()
    r = client.get(f"/incidents/{incident_id}/similar", params={"k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("vector_search", "aggregation_fallback")
    matches = body["matches"]
    assert len(matches) == 3
    assert all(m["root_cause"] == "pump_failure" for m in matches)
    assert incident_id not in [m["_id"] for m in matches]
    scores = [m["score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert matches[0]["actions"]
    assert matches[0]["recovery_minutes"] > 0


def test_fallback_score_matches_domain_similarity(history):
    query = fingerprint(PUMP_EVIDENCE)
    by_id = {i.id: i for i in history}
    rows, _ = incident_service.incidents_repo.find_similar(query, None, 5)
    for row in rows:
        expected = similarity(query, fingerprint(by_id[row["_id"]].evidence))
        assert row["score"] == pytest.approx(expected, rel=1e-6)


def test_investigating_attaches_similar_incidents_and_event(history, client: TestClient):
    incident_id = _open_pump_incident("pond-06")
    r = client.patch(f"/incidents/{incident_id}", params={"state": "investigating", "action": "check pump"})
    assert r.status_code == 200
    doc = incidents_collection().find_one({"_id": incident_id})
    assert len(doc["similar_incidents"]) == 3
    assert doc["similar_incidents"][0]["root_cause"] == "pump_failure"
    event = events_collection().find_one({"incident_id": incident_id, "event": "incident_similar_matched"})
    assert event and len(event["details"]["matches"]) == 3


def test_failed_similar_lookup_does_not_block_transition(history, client: TestClient, monkeypatch):
    incident_id = _open_pump_incident("pond-04")

    def boom(*args, **kwargs):
        raise RuntimeError("search down")

    monkeypatch.setattr(incident_service.incidents_repo, "find_similar", boom)
    r = client.patch(f"/incidents/{incident_id}", params={"state": "investigating", "action": "check pump"})
    assert r.status_code == 200
    doc = incidents_collection().find_one({"_id": incident_id})
    assert doc["state"] == "investigating"
    assert "similar_incidents" not in doc
    assert events_collection().find_one({"incident_id": incident_id, "event": "incident_action"})
    assert not events_collection().find_one({"incident_id": incident_id, "event": "incident_similar_matched"})


def test_similar_unknown_incident_returns_404(client: TestClient):
    assert client.get("/incidents/inc-missing/similar").status_code == 404
    assert client.get("/incidents/inc-missing/similar", params={"k": 0}).status_code == 422
