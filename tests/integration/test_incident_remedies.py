"""Applying a remedy from an incident: recorded on the incident, Resolving, and the simulated cause ends."""

import pytest

from app.db.mongo import events_collection, incidents_collection, ponds_collection
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.services import incidents as incident_service

POND = "pond-06"
CRITICAL = Finding(Severity.CRITICAL, "Quat hong", "0 W", "pump_failure")


@pytest.fixture(autouse=True)
def _close_incident_and_repair(client):
    """Leave the pond as other tests expect it: no active incident, aerator running."""
    yield
    incident_service.auto_recover(POND)
    client.post(f"/ponds/{POND}/pump/fail", params={"failed": "false"})


def _open_pump_incident(client) -> str:
    incident_service.auto_recover(POND)
    assert client.post(f"/ponds/{POND}/pump/fail", params={"failed": "true"}).status_code == 200
    return incident_service.open_or_update(POND, CRITICAL, IncidentEvidence())


def test_repair_aerator_from_incident_moves_to_resolving_and_repairs_the_pump(client):
    incident_id = _open_pump_incident(client)

    r = client.post(f"/incidents/{incident_id}/remedies/repair_aerator", params={"note": "Thay tu"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "resolving"

    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "resolving"  # never recovered by the click: only new readings prove it
    assert inc["actions"][-1]["action"] == "Sửa quạt nước: Thay tu"
    assert ponds_collection().find_one({"_id": POND})["pump"]["failed"] is False
    action = events_collection().find_one({"incident_id": incident_id, "event": "incident_action"})
    assert action["details"] == {
        "from_state": "open",
        "state": "resolving",
        "action": "Sửa quạt nước: Thay tu",
        "remedy": "repair_aerator",
    }


def test_timeline_shows_the_remedy_and_suggestions(client):
    incident_id = _open_pump_incident(client)
    client.post(f"/incidents/{incident_id}/remedies/repair_aerator")

    body = client.get(f"/incidents/{incident_id}/timeline").json()
    events = [e["event"] for e in body["timeline"]]
    assert "remedy_applied" in events and events.count("pump_state_change") >= 2  # failed, then repaired
    assert [s["remedy"] for s in body["suggested_remedies"]] == ["repair_aerator"]


def test_remedy_on_a_recovered_incident_is_rejected(client):
    incident_id = _open_pump_incident(client)
    incident_service.auto_recover(POND)
    assert client.post(f"/incidents/{incident_id}/remedies/repair_aerator").status_code == 409


def test_unknown_incident_and_remedy(client):
    assert client.post("/incidents/inc-missing/remedies/cut_feed").status_code == 404
    incident_id = _open_pump_incident(client)
    assert client.post(f"/incidents/{incident_id}/remedies/pray").status_code == 422


def test_a_second_remedy_on_a_resolving_incident_is_recorded(client):
    incident_service.auto_recover(POND)
    waste = Finding(Severity.WARNING, "NH3 cao", "0.2 mg/L", "nh3_out_of_range")
    incident_id = incident_service.open_or_update(POND, waste, IncidentEvidence())
    assert client.post(f"/incidents/{incident_id}/remedies/cut_feed").status_code == 200
    assert client.post(f"/incidents/{incident_id}/remedies/clean_bottom").status_code == 200

    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "resolving"
    assert [a["action"] for a in inc["actions"]] == ["Ngừng cho ăn", "Hút bùn đáy"]
