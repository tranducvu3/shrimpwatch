"""Vong doi su co. Moi thay doi su co (worker hay API) deu di qua day: ghi incident + event + metric 1 cho."""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import incidents as incidents_repo
from app.domain.incident_rules import Finding
from app.domain.models import Incident, IncidentEvidence, IncidentState

logger = get_logger("services.incidents")


class IncidentNotFound(LookupError):
    pass


def open_or_update(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str:
    """Cap nhat su co dang mo cua ao; neu chua co thi mo su co moi."""
    existing = incidents_repo.find_active(pond_id)
    if existing:
        incidents_repo.update_fields(
            existing["_id"],
            {
                "severity": finding.severity.value,
                "title": finding.title,
                "description": finding.description,
                "evidence": evidence.model_dump(),
                "root_cause": finding.root_cause,
            },
        )
        return existing["_id"]

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
    incidents_repo.insert(incident.model_dump(by_alias=True))
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
    """Worker tu dong dong su co dang mo khi ao da on dinh. Tra ve id su co neu co."""
    existing = incidents_repo.find_active(pond_id)
    if not existing:
        return None
    incidents_repo.set_state(existing["_id"], IncidentState.RECOVERED, action="worker_auto_recovered")
    events_repo.record("incident_recovered", pond_id, incident_id=existing["_id"])
    increment("incident.recovered", tags={"pond": pond_id})
    logger.info("incident auto-recovered", extra={"pond": pond_id, "incident_id": existing["_id"]})
    return existing["_id"]


def transition(incident_id: str, state: IncidentState, action: str | None = None) -> None:
    """Ky thuat vien chuyen buoc su co (Investigating/Resolving/Recovered), kem ghi chu hanh dong."""
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    pond_id = incident.get("pond_id")
    set_span_tags(pond_id=pond_id)
    if action:
        events_repo.record(
            "incident_action", pond_id, incident_id=incident_id, details={"state": state.value, "action": action}
        )
    incidents_repo.set_state(incident_id, state, action=action)


def list_incidents(state: IncidentState | None = None) -> list[dict]:
    return incidents_repo.list_recent(state)


def timeline(incident_id: str) -> dict:
    row = incidents_repo.timeline(incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    set_span_tags(pond_id=row.get("pond_id"))
    return row
