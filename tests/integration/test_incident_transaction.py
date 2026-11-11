"""An incident change and its `events` entry are committed together or not at all; validators guard the shape."""

import pytest
from pymongo.errors import WriteError

from app.core.clock import utcnow
from app.core.config import settings
from app.db.mongo import events_collection, incidents_collection, run_in_transaction
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, IncidentState, Severity
from app.services import incidents as incident_service

POND = "pond-71"
WARNING = Finding(Severity.WARNING, "Oxy giam nhanh", "rate 0.08", "do_out_of_range")


class Boom(RuntimeError):
    pass


def _fail_event_write(monkeypatch):
    def record(*_args, **_kwargs):
        raise Boom("events write failed")

    monkeypatch.setattr(incident_service.events_repo, "record", record)


def _open() -> str:
    incident_service.auto_recover(POND)
    return incident_service.open_or_update(POND, WARNING, IncidentEvidence(min_do=5.0))


def test_failed_event_write_rolls_back_the_state_change(monkeypatch):
    incident_id = _open()
    events_before = events_collection().count_documents({"incident_id": incident_id})
    _fail_event_write(monkeypatch)

    with pytest.raises(Boom):
        incident_service.transition(incident_id, IncidentState.INVESTIGATING, "check aerator")

    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "open" and inc.get("acknowledged_at") is None and inc.get("actions") == []
    assert events_collection().count_documents({"incident_id": incident_id}) == events_before


def test_failed_event_write_leaves_no_incident(monkeypatch):
    incident_service.auto_recover(POND)
    _fail_event_write(monkeypatch)

    with pytest.raises(Boom):
        incident_service.open_or_update(POND, WARNING, IncidentEvidence())

    assert incidents_collection().count_documents({"pond_id": POND, "state": "open"}) == 0


def test_auto_recover_writes_state_and_event_together():
    incident_id = _open()
    assert incident_service.auto_recover(POND) == incident_id
    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "recovered" and inc["recovered_at"] is not None
    assert events_collection().count_documents({"incident_id": incident_id, "event": "incident_recovered"}) == 1
    assert incident_service.auto_recover(POND) is None


def test_nested_transaction_joins_the_outer_one(monkeypatch):
    incident_id = _open()

    def both():
        incident_service.transition(incident_id, IncidentState.INVESTIGATING)
        raise Boom("outer step failed after the inner transition")

    with pytest.raises(Boom):
        run_in_transaction(both)
    assert incidents_collection().find_one({"_id": incident_id})["state"] == "open"


def test_validator_rejects_an_incident_with_an_unknown_state():
    now = utcnow()
    doc = {
        "_id": "inc-bad-state",
        "farm_id": settings.FARM_ID,
        "pond_id": "pond-72",
        "state": "on_fire",
        "severity": "warning",
        "title": "t",
        "created_at": now,
        "updated_at": now,
    }
    with pytest.raises(WriteError) as err:
        incidents_collection().insert_one(doc)
    assert err.value.code == 121  # DocumentValidationFailure


def test_validator_rejects_an_event_without_a_timestamp():
    with pytest.raises(WriteError) as err:
        events_collection().insert_one({"event": "incident_open", "pond_id": "pond-72"})
    assert err.value.code == 121
