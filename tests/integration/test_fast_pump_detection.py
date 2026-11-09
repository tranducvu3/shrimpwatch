"""A stopped aerator opens a critical incident at ingest, within two readings, and the worker agrees with it."""

from datetime import timedelta

import pytest
from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.core.config import settings
from app.db.mongo import events_collection, incidents_collection
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.services import incidents as incident_service


def _pump_batch(pond: str, status: float, seconds_ago: int = 0) -> dict:
    ts = (utcnow() - timedelta(seconds=seconds_ago)).isoformat()
    common = {"timestamp": ts, "farm_id": settings.FARM_ID, "pond_id": pond, "device_id": f"pump-{pond}"}
    return {
        "records": [
            {**common, "device_type": "pump", "metric": "pump_status", "value": status, "source": "sensor"},
            {
                **common,
                "device_type": "pump",
                "metric": "pump_power",
                "value": 0.0 if status == 0 else 1500.0,
                "source": "sensor",
            },
        ]
    }


def _active(pond: str) -> list[dict]:
    return list(incidents_collection().find({"pond_id": pond, "state": {"$ne": "recovered"}}))


def test_two_off_readings_open_a_critical_incident_at_ingest(client):
    pond = "pond-81"
    assert client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=30)).status_code == 202
    assert _active(pond) == []  # one reading is not enough: could be a glitch

    assert client.post("/ingest/batch", json=_pump_batch(pond, 0.0)).status_code == 202
    [inc] = _active(pond)
    assert (inc["severity"], inc["root_cause"]) == ("critical", "pump_failure")
    assert inc["evidence"]["notes"]["detected_by"] == "ingest"
    assert events_collection().count_documents({"incident_id": inc["_id"], "event": "incident_open"}) == 1


def test_off_then_on_does_not_open_an_incident(client):
    pond = "pond-82"
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=30))
    client.post("/ingest/batch", json=_pump_batch(pond, 1.0))
    assert _active(pond) == []


def test_backfilled_off_readings_do_not_open_an_incident(client):
    pond = "pond-83"
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=3600))
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=3570))
    assert _active(pond) == []


def test_ingest_escalates_a_warning_but_keeps_the_worker_evidence_of_a_critical(client):
    pond = "pond-84"
    warning = Finding(Severity.WARNING, "DO giam", "rate", "do_out_of_range")
    incident_id = incident_service.open_or_update(pond, warning, IncidentEvidence(min_do=5.0))
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=30))
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0))
    inc = incidents_collection().find_one({"_id": incident_id})
    assert (inc["severity"], inc["root_cause"]) == ("critical", "pump_failure")

    # Already critical: a further off reading must not overwrite the evidence
    incidents_collection().update_one({"_id": incident_id}, {"$set": {"evidence.min_do": 4.2}})
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0))
    assert incidents_collection().find_one({"_id": incident_id})["evidence"]["min_do"] == 4.2
    assert len(_active(pond)) == 1


def test_worker_does_not_auto_recover_while_aerator_is_stopped(client):
    from app.worker.pond_check import check_pond

    pond = "pond-85"
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0, seconds_ago=30))
    client.post("/ingest/batch", json=_pump_batch(pond, 0.0))
    # DO still healthy and only 2 power readings: the old rule would have called the pond recovered
    check_pond(pond, {"do_drop_rate_mg_per_l_per_min": 0.0, "min_do": 7.0})
    [inc] = _active(pond)
    assert inc["root_cause"] == "pump_failure"


def test_database_rejects_a_second_active_incident_for_a_pond():
    pond = "pond-86"
    base = {"farm_id": settings.FARM_ID, "pond_id": pond, "severity": "warning", "title": "t", "description": "d"}
    incidents_collection().insert_one({"_id": "inc-dup-1", "state": "open", **base})
    with pytest.raises(DuplicateKeyError):
        incidents_collection().insert_one({"_id": "inc-dup-2", "state": "investigating", **base})
    # Recovered incidents do not count
    incidents_collection().insert_one({"_id": "inc-dup-3", "state": "recovered", **base})


def _miss_first_read(monkeypatch):
    """Simulate a race: the first find_active misses the incident another writer just opened."""
    from app.db.repositories import incidents as incidents_repo

    real = incidents_repo.find_active
    calls = {"n": 0}

    def find_active(pond_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(pond_id)

    monkeypatch.setattr(incidents_repo, "find_active", find_active)


def test_ingest_losing_a_race_to_the_worker_keeps_the_worker_incident(monkeypatch):
    """The worker opens the pond's incident between the ingest check's read and its insert."""
    pond = "pond-87"
    worker = Finding(Severity.CRITICAL, "Quat hong", "worker: 0 W, DO 6.1", "pump_failure")
    worker_id = incident_service.open_or_update(pond, worker, IncidentEvidence(min_do=6.1))
    _miss_first_read(monkeypatch)

    ingest = Finding(Severity.CRITICAL, "Quat hong", "ingest: 2 zero readings", "pump_failure")
    assert incident_service.open_or_escalate(pond, ingest, IncidentEvidence()) is None

    [inc] = _active(pond)
    assert inc["_id"] == worker_id
    assert (inc["description"], inc["evidence"]["min_do"]) == ("worker: 0 W, DO 6.1", 6.1)
    assert events_collection().count_documents({"pond_id": pond, "event": "incident_open"}) == 1


def test_worker_losing_a_race_updates_the_existing_incident(monkeypatch):
    pond = "pond-89"
    first_id = incident_service.open_or_escalate(
        pond, Finding(Severity.CRITICAL, "Quat", "ingest", "pump_failure"), IncidentEvidence()
    )
    _miss_first_read(monkeypatch)

    worker = Finding(Severity.CRITICAL, "Quat", "worker", "pump_failure")
    assert incident_service.open_or_update(pond, worker, IncidentEvidence(min_do=5.9)) == first_id
    [inc] = _active(pond)
    assert inc["evidence"]["min_do"] == 5.9
