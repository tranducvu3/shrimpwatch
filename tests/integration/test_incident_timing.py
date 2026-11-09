"""Detection delay, time to acknowledge and time to recover, and the Datadog Events mirroring the lifecycle."""

from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.db.mongo import events_collection, incidents_collection
from app.domain.incident_rules import Finding
from app.domain.models import IncidentEvidence, IncidentState, Severity
from app.services import incidents as incident_service

CRITICAL = Finding(Severity.CRITICAL, "Quat hong", "0 W", "pump_failure")
WARNING = Finding(Severity.WARNING, "DO giam", "rate", "do_out_of_range")


@pytest.fixture
def sent(monkeypatch):
    """Capture what the service sends to Datadog instead of a DogStatsD socket."""
    calls = {"distribution": [], "increment": [], "event": []}
    monkeypatch.setattr(
        incident_service,
        "distribution",
        lambda name, value, tags=None: calls["distribution"].append((name, value, tags)),
    )
    monkeypatch.setattr(
        incident_service, "increment", lambda name, value=1, tags=None: calls["increment"].append((name, tags))
    )
    monkeypatch.setattr(
        incident_service, "event", lambda title, text, **kw: calls["event"].append((title, {**kw, "text": text}))
    )
    return calls


def _fault(pond: str, event: str, seconds_ago: float, **details) -> None:
    events_collection().insert_one(
        {"timestamp": utcnow() - timedelta(seconds=seconds_ago), "pond_id": pond, "event": event, "details": details}
    )


def test_detection_delay_is_measured_from_the_injected_fault(sent):
    pond = "pond-91"
    _fault(pond, "pump_state_change", 45, failed=True)
    incident_id = incident_service.open_or_update(pond, CRITICAL, IncidentEvidence())

    inc = incidents_collection().find_one({"_id": incident_id})
    assert 44 <= inc["detection_delay_sec"] <= 50
    [(name, value, tags)] = sent["distribution"]
    assert name == "incident.detection_delay" and value == inc["detection_delay_sec"]
    assert ("incident.detected", {**tags, "within_slo": "true"}) in sent["increment"]
    [(title, kw)] = sent["event"]
    assert "incident_open" in title and kw["alert_type"] == "error" and kw["aggregation_key"] == incident_id


def test_slow_detection_is_counted_outside_the_slo(sent):
    pond = "pond-92"
    _fault(pond, "condition_start", 600, condition="heavy_rain")
    incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    [(_, tags)] = [c for c in sent["increment"] if c[0] == "incident.detected"]
    assert tags["within_slo"] == "false"


def test_no_known_fault_means_no_detection_delay(sent):
    pond = "pond-93"
    _fault(pond, "pump_state_change", 30, failed=False)  # a repair is not a fault
    incident_id = incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["fault_started_at"] is None and inc["detection_delay_sec"] is None
    assert sent["distribution"] == []


def test_a_fault_from_before_the_previous_incident_closed_is_not_reused(sent):
    pond = "pond-94"
    _fault(pond, "scenario_start", 300, scenario="do")
    incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    incident_service.auto_recover(pond)
    second = incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    assert incidents_collection().find_one({"_id": second})["detection_delay_sec"] is None


def test_acknowledge_and_recover_times_are_stored_and_sent(sent):
    pond = "pond-95"
    incident_id = incident_service.open_or_update(pond, CRITICAL, IncidentEvidence())
    incident_service.transition(incident_id, IncidentState.INVESTIGATING, "KT dien quat")
    incident_service.transition(incident_id, IncidentState.RESOLVING, "Thay tu")
    acknowledged = incidents_collection().find_one({"_id": incident_id})["acknowledged_at"]
    assert acknowledged is not None

    incident_service.auto_recover(pond)
    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["state"] == "recovered" and inc["recovered_at"] >= acknowledged
    assert inc["acknowledged_at"] == acknowledged  # moving on from investigating does not re-acknowledge
    names = [name for name, _, _ in sent["distribution"]]
    assert names.count("incident.time_to_acknowledge") == 1 and names.count("incident.time_to_recover") == 1
    whats = [kw["tags"]["incident_event"] for _, kw in sent["event"]]
    assert whats == ["incident_open", "incident_investigating", "incident_resolving", "incident_recovered"]


def test_operator_closing_a_false_alarm_records_recovery(sent):
    pond = "pond-96"
    incident_id = incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    incident_service.transition(incident_id, IncidentState.RECOVERED, "Bao nham")
    inc = incidents_collection().find_one({"_id": incident_id})
    assert inc["recovered_at"] is not None and inc["acknowledged_at"] is not None
    assert inc["actions"][-1]["action"] == "Bao nham"
    assert sent["event"][-1][1]["alert_type"] == "success"


def test_escalation_is_sent_as_an_event(sent):
    pond = "pond-97"
    incident_service.open_or_update(pond, WARNING, IncidentEvidence())
    incident_service.open_or_update(pond, CRITICAL, IncidentEvidence())
    title, kw = sent["event"][-1]
    assert "incident_escalated" in title and kw["tags"]["root_cause"] == "pump_failure"


def test_open_event_text_tells_the_farmer_what_to_do(sent, monkeypatch):
    monkeypatch.setattr(incident_service.settings, "PUBLIC_BASE_URL", "https://farm.example/")
    incident_service.open_or_update("pond-98", CRITICAL, IncidentEvidence())
    title, kw = sent["event"][0]
    assert "pond-98" in title
    assert kw["text"].splitlines() == [
        "%%%",
        "**Mức độ:** 🔴 nghiêm trọng",
        "",
        "**Tình trạng:** 0 W",
        "",
        "**Đề xuất xử lý:**",
        "- Sửa quạt nước",
        "",
        "**[👉 Mở bảng điều khiển ao pond-98](https://farm.example/control?ao=pond-98)**",
        "%%%",
    ]


def test_escalation_text_names_both_severities_and_omits_an_unset_link(sent, monkeypatch):
    monkeypatch.setattr(incident_service.settings, "PUBLIC_BASE_URL", "")
    incident_service.open_or_update("pond-79", WARNING, IncidentEvidence())
    incident_service.open_or_update("pond-79", CRITICAL, IncidentEvidence())
    _, kw = sent["event"][-1]
    assert kw["text"].splitlines() == [
        "%%%",
        "**Nâng mức:** 🟠 cảnh báo → 🔴 nghiêm trọng",
        "",
        "**Tình trạng:** 0 W",
        "",
        "**Đề xuất xử lý:**",
        "- Sửa quạt nước",
        "%%%",
    ]
