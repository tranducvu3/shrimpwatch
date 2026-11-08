"""Incident lifecycle. Every incident change (worker or API) goes through here: writes incident + event + metric in one place."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.datadog_links import incident_links
from app.core.log import get_logger
from app.core.telemetry import distribution, event, gauge, increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import incidents as incidents_repo
from app.domain.conditions import REMEDIES, Remedy
from app.domain.incident_lifecycle import ensure_transition, is_escalation
from app.domain.incident_rules import Finding
from app.domain.models import Incident, IncidentEvidence, IncidentState
from app.domain.remedies import suggested_remedies
from app.services import ponds as pond_service

logger = get_logger("services.incidents")


class IncidentNotFound(LookupError):
    pass


_ALERT_TYPE = {"critical": "error", "warning": "warning"}


def _datadog_event(incident: dict, what: str, text: str, alert_type: str | None = None) -> None:
    """Mirror an incident change as a Datadog Event, grouped by incident id, so Datadog shows the same lifecycle."""
    severity = incident.get("severity", "")
    event(
        f"[ShrimpWatch] {incident['pond_id']}: {incident.get('title', '')} ({what})",
        text,
        alert_type=alert_type or _ALERT_TYPE.get(severity, "info"),
        aggregation_key=incident["_id"],
        tags={**_timing_tags(incident), "incident_id": incident["_id"], "incident_event": what},
    )


def _seconds(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds(), 1)


def _timing_tags(incident: dict) -> dict:
    return {"pond": incident["pond_id"], "severity": incident.get("severity"), "root_cause": incident.get("root_cause")}


def _update_active(existing: dict, finding: Finding, evidence: IncidentEvidence) -> str:
    """An incident keeps the most severe cause seen so far: it only escalates, never downgrades.

    E.g. pump failure (critical), then the pump restarts while DO is still falling (warning): the incident stays pump_failure.
    """
    incident_id = existing["_id"]
    fields: dict = {"evidence": evidence.model_dump()}
    current = existing.get("severity")
    if is_escalation(current, finding.severity):
        fields |= {
            "severity": finding.severity.value,
            "title": finding.title,
            "description": finding.description,
            "root_cause": finding.root_cause,
        }
        events_repo.record(
            "incident_escalated",
            existing["pond_id"],
            incident_id=incident_id,
            details={
                "from_severity": current,
                "to_severity": finding.severity.value,
                "from_root_cause": existing.get("root_cause"),
                "to_root_cause": finding.root_cause,
            },
        )
        increment(
            "incident.escalated",
            tags={"pond": existing["pond_id"], "severity": finding.severity.value, "root_cause": finding.root_cause},
        )
        _datadog_event(
            {**existing, **fields},
            "incident_escalated",
            f"{current} -> {finding.severity.value}. {finding.description}",
        )
    elif finding.root_cause == existing.get("root_cause"):
        # Same cause: refresh the description with the latest data
        fields["description"] = finding.description
    incidents_repo.update_fields(incident_id, fields)
    return incident_id


def open_or_update(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str:
    """Update the pond's active incident; open a new one if there is none."""
    # Twice at most: if another writer opens the pond's incident between our read and insert, update that one
    for _ in range(2):
        existing = incidents_repo.find_active(pond_id)
        if existing:
            return _update_active(existing, finding, evidence)
        incident_id = _open(pond_id, finding, evidence)
        if incident_id:
            return incident_id
    raise RuntimeError(f"could not open or update an incident for {pond_id}")


def open_or_escalate(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str | None:
    """Open an incident, or escalate the active one; leave an equally or more severe active incident untouched.

    For the fast ingest check: it must not overwrite the worker's fuller evidence every batch, nor when it loses a
    race with the worker.
    """
    for _ in range(2):
        existing = incidents_repo.find_active(pond_id)
        if existing:
            return (
                _update_active(existing, finding, evidence)
                if is_escalation(existing.get("severity"), finding.severity)
                else None
            )
        incident_id = _open(pond_id, finding, evidence)
        if incident_id:
            return incident_id
    return None


def _open(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str | None:
    """Insert a new incident; None if the pond already has an active one (unique index one_active_incident_per_pond)."""
    incident_id = f"inc-{uuid.uuid4().hex[:10]}"
    now = utcnow()
    fault_started_at = _fault_start(pond_id, now)
    incident = Incident(
        id=incident_id,
        farm_id=settings.FARM_ID,
        pond_id=pond_id,
        severity=finding.severity,
        title=finding.title,
        description=finding.description,
        evidence=evidence,
        root_cause=finding.root_cause,
        created_at=now,
        updated_at=now,
        fault_started_at=fault_started_at,
        detection_delay_sec=_seconds(fault_started_at, now) if fault_started_at else None,
    )
    doc = incident.model_dump(by_alias=True)
    if not incidents_repo.insert(doc):
        return None
    events_repo.record(
        "incident_open",
        pond_id,
        incident_id=incident_id,
        details={"severity": finding.severity.value, "title": finding.title},
    )
    increment(
        "incident.open", tags={"pond": pond_id, "severity": finding.severity.value, "root_cause": finding.root_cause}
    )
    _report_detection(doc)
    _datadog_event(doc, "incident_open", finding.description)
    logger.error(
        "incident opened",
        extra={
            "incident_id": incident_id,
            "pond": pond_id,
            "severity": finding.severity.value,
            "detection_delay_sec": doc["detection_delay_sec"],
        },
    )
    return incident_id


def _fault_start(pond_id: str, now: datetime) -> datetime | None:
    """The injected fault this incident comes from: the latest one since the pond's previous incident closed."""
    since = now - timedelta(seconds=settings.FAULT_LOOKBACK_SEC)
    closed_at = incidents_repo.last_closed_at(pond_id)
    if closed_at and closed_at > since:
        since = closed_at
    return events_repo.latest_fault_start(pond_id, since, now)


def _report_detection(doc: dict) -> None:
    """Detection delay and the SLO counter; nothing when no fault is known (real sensors have no ground truth)."""
    delay = doc.get("detection_delay_sec")
    if delay is None:
        return
    tags = _timing_tags(doc)
    distribution("incident.detection_delay", delay, tags=tags)
    increment("incident.detected", tags={**tags, "within_slo": str(delay <= settings.DETECTION_SLO_SEC).lower()})


def _close(incident: dict, action: str | None, extra_fields: dict | None = None) -> None:
    """Move to Recovered with the recovery time; shared by the worker and the operator."""
    now = utcnow()
    fields = {"recovered_at": now, **(extra_fields or {})}
    incidents_repo.set_state(incident["_id"], IncidentState.RECOVERED, action=action, fields=fields)
    seconds = _seconds(incident["created_at"], now)
    distribution("incident.time_to_recover", seconds, tags=_timing_tags(incident))
    _datadog_event(incident, "incident_recovered", f"Recovered after {seconds:.0f} s.", alert_type="success")


def auto_recover(pond_id: str) -> str | None:
    """The worker closes the active incident automatically once the pond is stable. Returns the incident id if any."""
    existing = incidents_repo.find_active(pond_id)
    if not existing:
        return None
    _close(existing, action="worker_auto_recovered")
    events_repo.record("incident_recovered", pond_id, incident_id=existing["_id"])
    increment("incident.recovered", tags={"pond": pond_id})
    logger.info("incident auto-recovered", extra={"pond": pond_id, "incident_id": existing["_id"]})
    return existing["_id"]


def transition(incident_id: str, state: IncidentState, action: str | None = None, remedy: Remedy | None = None) -> None:
    """A technician moves the incident to the next step (Investigating/Resolving/Recovered), with an action note."""
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    pond_id = incident.get("pond_id")
    set_span_tags(pond_id=pond_id)
    from_state = incident.get("state")
    ensure_transition(from_state, state)
    # Every transition writes to events (even without a note) so the timeline has no gaps
    details = {"from_state": from_state, "state": state.value}
    if action:
        details["action"] = action
    if remedy:
        details["remedy"] = remedy.value
    events_repo.record("incident_action", pond_id, incident_id=incident_id, details=details)

    fields = {}
    if from_state == IncidentState.OPEN.value and state != IncidentState.OPEN and not incident.get("acknowledged_at"):
        fields["acknowledged_at"] = utcnow()
        distribution(
            "incident.time_to_acknowledge",
            _seconds(incident["created_at"], fields["acknowledged_at"]),
            tags=_timing_tags(incident),
        )
    if state == IncidentState.RECOVERED and from_state != IncidentState.RECOVERED.value:
        _close(incident, action, fields)
        return
    incidents_repo.set_state(incident_id, state, action=action, fields=fields)
    if state.value != from_state:
        _datadog_event(incident, f"incident_{state.value}", action or f"{from_state} -> {state.value}")


def apply_remedy(incident_id: str, remedy: Remedy, note: str | None = None) -> dict:
    """The operator applies a remedy to an incident: recorded on the incident, which moves to Resolving.

    Only new readings can prove the pond recovered, so the worker (not this call) moves it to Recovered.
    On the simulated farm the remedy also ends the simulated cause (pond_service.apply_remedy).
    """
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    label = REMEDIES[remedy].label
    # Record on the incident first: if it was closed meanwhile this raises (409) before the farm is changed
    transition(incident_id, IncidentState.RESOLVING, f"{label}: {note}" if note else label, remedy=remedy)
    cleared = pond_service.apply_remedy(incident["pond_id"], remedy)
    return {
        "incident_id": incident_id,
        "remedy": remedy.value,
        "state": IncidentState.RESOLVING.value,
        "cleared": cleared,
    }


def report_active(pond_ids: list[str]) -> None:
    """0/1 gauge per pond per cycle: the dashboard counts CURRENTLY active incidents (the incident.open counter only counts openings)."""
    active = incidents_repo.active_pond_ids()
    for pid in pond_ids:
        gauge("incident.active", 1 if pid in active else 0, tags={"pond": pid})


def list_incidents(state: IncidentState | None = None, limit: int = 50) -> list[dict]:
    return incidents_repo.list_recent(state, limit)


def timeline(incident_id: str) -> dict:
    row = incidents_repo.timeline(incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    set_span_tags(pond_id=row.get("pond_id"))
    now = utcnow()
    row["links"] = incident_links(
        incident_id, row["pond_id"], row.get("created_at") or now, row.get("recovered_at") or now
    )
    row["suggested_remedies"] = [
        {"remedy": r.value, "label": REMEDIES[r].label, "description": REMEDIES[r].description}
        for r in suggested_remedies(row.get("root_cause"))
    ]
    return row
