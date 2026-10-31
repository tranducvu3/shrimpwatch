from app.db.mongo import events_collection, incidents_collection
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, Severity
from app.services import incidents as incident_service

POND = "pond-02"

WARNING = Finding(Severity.WARNING, "Oxy giam nhanh", "rate 0.08", "do_anomaly_drop")
CRITICAL = Finding(Severity.CRITICAL, "Quat hong", "0 W", "pump_failure")


def _clear_active():
    # Dong su co dang mo (neu co) qua service thay vi xoa du lieu
    incident_service.auto_recover(POND)


def test_incident_escalates_and_records_event():
    _clear_active()
    incident_id = incident_service.open_or_update(POND, WARNING, IncidentEvidence(min_do=5.0))
    assert incident_service.open_or_update(POND, CRITICAL, IncidentEvidence(min_do=4.5)) == incident_id

    inc = incidents_collection().find_one({"_id": incident_id})
    assert (inc["severity"], inc["root_cause"], inc["title"]) == ("critical", "pump_failure", "Quat hong")
    event = events_collection().find_one({"incident_id": incident_id, "event": "incident_escalated"})
    assert event["details"] == {
        "from_severity": "warning",
        "to_severity": "critical",
        "from_root_cause": "do_anomaly_drop",
        "to_root_cause": "pump_failure",
    }


def test_incident_is_not_downgraded_but_evidence_refreshes():
    _clear_active()
    incident_id = incident_service.open_or_update(POND, CRITICAL, IncidentEvidence(min_do=4.5))
    incident_service.open_or_update(POND, WARNING, IncidentEvidence(min_do=4.1))

    inc = incidents_collection().find_one({"_id": incident_id})
    assert (inc["severity"], inc["root_cause"], inc["description"]) == ("critical", "pump_failure", "0 W")
    assert inc["evidence"]["min_do"] == 4.1
    assert events_collection().count_documents({"incident_id": incident_id, "event": "incident_escalated"}) == 0


def test_same_cause_refreshes_description():
    _clear_active()
    incident_id = incident_service.open_or_update(POND, WARNING, IncidentEvidence())
    newer = Finding(Severity.WARNING, WARNING.title, "rate 0.12", WARNING.root_cause)
    incident_service.open_or_update(POND, newer, IncidentEvidence())
    assert incidents_collection().find_one({"_id": incident_id})["description"] == "rate 0.12"
