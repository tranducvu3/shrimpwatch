"""The Datadog auto-remediation workflow switches on the backup aerator through POST /automation/remediate."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.mongo import events_collection, incidents_collection
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.services import incidents as incident_service

POND = "pond-05"
TOKEN = "test-automation-token"
PUMP = Finding(Severity.CRITICAL, "Quat hong", "0 W", "pump_failure")
SALINITY = Finding(Severity.WARNING, "Do man thap", "8 ppt", "salinity_out_of_range")


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "AUTOMATION_TOKEN", TOKEN)


def _remediate(client: TestClient, pond: str = POND, token: str = TOKEN, run_id: str | None = "run-1"):
    body = {"pond_id": pond, **({"run_id": run_id} if run_id else {})}
    return client.post("/automation/remediate", json=body, headers={"X-Automation-Token": token})


def _open(finding: Finding) -> str:
    incident_service.auto_recover(POND)
    return incident_service.open_or_update(POND, finding, IncidentEvidence())


def test_disabled_without_a_token_and_rejects_a_wrong_one(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "AUTOMATION_TOKEN", "")
    assert _remediate(client).status_code == 503
    monkeypatch.setattr(settings, "AUTOMATION_TOKEN", TOKEN)
    assert _remediate(client, token="wrong").status_code == 401
    assert _remediate(client, pond="pond-1; drop").status_code == 422


def test_stopped_aerator_gets_the_backup_aerator_once(client: TestClient, token):
    client.post(f"/ponds/{POND}/conditions/aerator_failure")
    incident_id = _open(PUMP)

    r = _remediate(client)
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["remedy"], body["state"]) == ("applied", "backup_aerator", "resolving")
    assert body["cleared"] == ["aerator_failure"]

    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "resolving" and inc["remedies"] == ["backup_aerator"]
    action = events_collection().find_one({"incident_id": incident_id, "event": "incident_action"})
    assert action["details"]["by"] == "datadog_workflow" and "run-1" in action["details"]["action"]

    # A workflow retry or a second alert does not apply it twice
    again = _remediate(client).json()
    assert (again["status"], again["reason"]) == ("skipped", "already_applied")
    client.delete(f"/ponds/{POND}/conditions")


def test_only_causes_with_an_automatic_remedy_are_touched(client: TestClient, token):
    incident_id = _open(SALINITY)
    assert _remediate(client).json()["reason"] == "no_automatic_remedy"
    assert incidents_collection().find_one({"_id": incident_id})["state"] == "open"

    incident_service.auto_recover(POND)
    assert _remediate(client, run_id=None).json()["reason"] == "no_active_incident"
