"""Applying a remedy from an incident: recorded on the incident, Resolving, and the simulated cause ends."""

from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.core.config import settings
from app.db.mongo import events_collection, incidents_collection, metrics_collection, ponds_collection
from app.db.repositories.metrics import pack_ts_doc
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.domain.remedy_outcome import RemedyOutcome
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
    assert set(body["links"]) == {"dashboard", "logs", "traces", "events"}
    assert f"%40pond%3A{POND}" in body["links"]["logs"]


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


def test_timeline_of_an_active_incident_includes_pond_events_after_its_last_update(client):
    from datetime import timedelta

    from app.core.clock import utcnow

    incident_id = _open_pump_incident(client)
    # The incident's last change was 10 s ago; a remedy event was written 5 s ago (after it)
    incidents_collection().update_one({"_id": incident_id}, {"$set": {"updated_at": utcnow() - timedelta(seconds=10)}})
    events_collection().insert_one(
        {
            "timestamp": utcnow() - timedelta(seconds=5),
            "pond_id": POND,
            "event": "remedy_applied",
            "details": {"remedy": "cut_feed"},
        }
    )
    events = [e["event"] for e in client.get(f"/incidents/{incident_id}/timeline").json()["timeline"]]
    assert "remedy_applied" in events


def test_incident_list_is_limited(client):
    assert client.get("/incidents", params={"limit": 0}).status_code == 422
    assert len(client.get("/incidents", params={"limit": 1}).json()) <= 1


def _do_reading(value: float) -> None:
    """A DO reading taken now: newer than any other test's, so it is the one the check reads."""
    doc = {
        "timestamp": utcnow(),
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


def _apply(client, incident_id: str, remedy: str) -> dict:
    assert client.post(f"/incidents/{incident_id}/remedies/{remedy}").status_code == 200
    return incidents_collection().find_one({"_id": incident_id})


def test_a_remedy_that_does_not_lift_do_fails_and_investigating_again_clears_it(client):
    incident_id = _open_pump_incident(client)
    _do_reading(2.8)
    inc = _apply(client, incident_id, "cut_feed")  # not what a broken aerator needs
    check = inc["remedy_check"]
    assert inc["remedies"] == ["cut_feed"]
    assert (check["metric"], check["baseline"], check["outcome"]) == ("do", 2.8, "pending")

    applied_at = check["applied_at"]
    assert incident_service.check_remedy(POND, applied_at + timedelta(seconds=30)) == RemedyOutcome.PENDING
    _do_reading(2.7)
    assert incident_service.check_remedy(POND, applied_at + timedelta(seconds=61)) == RemedyOutcome.FAILED
    assert incident_service.check_remedy(POND, applied_at + timedelta(seconds=91)) is None  # judged once

    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "resolving"
    assert (inc["remedy_check"]["outcome"], inc["remedy_check"]["value"]) == ("failed", 2.7)
    failed = events_collection().find_one({"incident_id": incident_id, "event": "remedy_failed"})
    assert failed["details"] == {"metric": "do", "baseline": 2.8, "value": 2.7, "remedies": ["cut_feed"]}

    assert client.patch(f"/incidents/{incident_id}", params={"state": "investigating"}).status_code == 200
    inc = incidents_collection().find_one({"_id": incident_id})
    assert (inc["state"], inc["remedies"], inc["remedy_check"]) == ("investigating", [], None)
    assert incident_service.check_remedy(POND) is None


def test_a_remedy_that_lifts_do_keeps_the_incident_resolving(client):
    incident_id = _open_pump_incident(client)
    _do_reading(2.8)
    applied_at = _apply(client, incident_id, "repair_aerator")["remedy_check"]["applied_at"]
    _do_reading(3.1)

    assert incident_service.check_remedy(POND, applied_at + timedelta(seconds=61)) == RemedyOutcome.IMPROVING
    inc = incidents_collection().find_one({"_id": incident_id})
    assert (inc["state"], inc["remedy_check"]["outcome"]) == ("resolving", "improving")


def test_another_remedy_restarts_the_check(client):
    incident_id = _open_pump_incident(client)
    _do_reading(2.8)
    first = _apply(client, incident_id, "cut_feed")["remedy_check"]["applied_at"]
    inc = _apply(client, incident_id, "repair_aerator")
    assert inc["remedies"] == ["cut_feed", "repair_aerator"]
    assert inc["remedy_check"]["applied_at"] > first and inc["remedy_check"]["outcome"] == "pending"


def test_a_pond_back_to_normal_means_the_remedy_worked_before_the_minute_is_up(client):
    incident_id = _open_pump_incident(client)
    _do_reading(6.15)
    applied_at = _apply(client, incident_id, "repair_aerator")["remedy_check"]["applied_at"]
    _do_reading(6.10)  # DO has not risen yet, but the pump runs and no rule fires

    later = applied_at + timedelta(seconds=20)
    assert incident_service.check_remedy(POND, later, still_abnormal=False) == RemedyOutcome.IMPROVING
    check = incidents_collection().find_one({"_id": incident_id})["remedy_check"]
    assert (check["outcome"], check["normal"]) == ("improving", True)
    assert events_collection().find_one({"incident_id": incident_id, "event": "remedy_normal"})
