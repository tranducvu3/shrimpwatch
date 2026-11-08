"""Incident lifecycle. Every incident change (worker or API) goes through here: writes incident + event + metric in one place."""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import incidents as incidents_repo
from app.domain.incident_lifecycle import ensure_transition, is_escalation
from app.domain.incident_rules import Finding
from app.domain.models import Incident, IncidentEvidence, IncidentState

logger = get_logger("services.incidents")


class IncidentNotFound(LookupError):
    pass


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
    elif finding.root_cause == existing.get("root_cause"):
        # Same cause: refresh the description with the latest data
        fields["description"] = finding.description
    incidents_repo.update_fields(incident_id, fields)
    return incident_id


def open_or_update(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str:
    """Update the pond's active incident; open a new one if there is none."""
    existing = incidents_repo.find_active(pond_id)
    if existing:
        return _update_active(existing, finding, evidence)
    return _open(pond_id, finding, evidence)


def open_or_escalate(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str | None:
    """Open an incident, or escalate the active one; leave an equally or more severe active incident untouched.

    For the fast ingest check: it must not overwrite the worker's fuller evidence every batch.
    """
    existing = incidents_repo.find_active(pond_id)
    if existing is None:
        return _open(pond_id, finding, evidence)
    if is_escalation(existing.get("severity"), finding.severity):
        return _update_active(existing, finding, evidence)
    return None


def _open(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str:
    incident_id = f"inc-{uuid.uuid4().hex[:10]}"
    incident = Incident(
        id=incident_id,
        farm_id=settings.FARM_ID,
        pond_id=pond_id,
        severity=finding.severity,
        title=finding.title,
        description=finding.description,
        evidence=evidence,
        root_cause=finding.root_cause,
    )
    if not incidents_repo.insert(incident.model_dump(by_alias=True)):
        # Another writer (ingest check or worker) opened the pond's incident a moment ago: update that one
        return open_or_update(pond_id, finding, evidence)
    events_repo.record(
        "incident_open",
        pond_id,
        incident_id=incident_id,
        details={"severity": finding.severity.value, "title": finding.title},
    )
    increment(
        "incident.open", tags={"pond": pond_id, "severity": finding.severity.value, "root_cause": finding.root_cause}
    )
    logger.error(
        "incident opened", extra={"incident_id": incident_id, "pond": pond_id, "severity": finding.severity.value}
    )
    return incident_id


def auto_recover(pond_id: str) -> str | None:
    """The worker closes the active incident automatically once the pond is stable. Returns the incident id if any."""
    existing = incidents_repo.find_active(pond_id)
    if not existing:
        return None
    incidents_repo.set_state(existing["_id"], IncidentState.RECOVERED, action="worker_auto_recovered")
    events_repo.record("incident_recovered", pond_id, incident_id=existing["_id"])
    increment("incident.recovered", tags={"pond": pond_id})
    logger.info("incident auto-recovered", extra={"pond": pond_id, "incident_id": existing["_id"]})
    return existing["_id"]


def transition(incident_id: str, state: IncidentState, action: str | None = None) -> None:
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
    events_repo.record("incident_action", pond_id, incident_id=incident_id, details=details)
    incidents_repo.set_state(incident_id, state, action=action)


def report_active(pond_ids: list[str]) -> None:
    """0/1 gauge per pond per cycle: the dashboard counts CURRENTLY active incidents (the incident.open counter only counts openings)."""
    active = incidents_repo.active_pond_ids()
    for pid in pond_ids:
        gauge("incident.active", 1 if pid in active else 0, tags={"pond": pid})


def list_incidents(state: IncidentState | None = None) -> list[dict]:
    return incidents_repo.list_recent(state)


def timeline(incident_id: str) -> dict:
    row = incidents_repo.timeline(incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    set_span_tags(pond_id=row.get("pond_id"))
    return row
