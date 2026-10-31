"""Vong doi su co. Moi thay doi su co (worker hay API) deu di qua day: ghi incident + event + metric 1 cho."""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.core.log import get_logger
from app.core.telemetry import gauge, increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import incidents as incidents_repo
from app.domain.incident_fingerprint import fingerprint, is_valid
from app.domain.incident_lifecycle import ensure_transition
from app.domain.incident_rules import Finding
from app.domain.models import Incident, IncidentEvidence, IncidentState

logger = get_logger("services.incidents")


class IncidentNotFound(LookupError):
    pass


def _to_doc(incident: Incident) -> dict:
    return {**incident.model_dump(by_alias=True), "fingerprint": fingerprint(incident.evidence)}


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
                "fingerprint": fingerprint(evidence),
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
    incidents_repo.insert(_to_doc(incident))
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
    ensure_transition(incident.get("state"), state)
    if action:
        events_repo.record(
            "incident_action", pond_id, incident_id=incident_id, details={"state": state.value, "action": action}
        )
    incidents_repo.set_state(incident_id, state, action=action)
    if state == IncidentState.INVESTIGATING:
        _attach_similar(incident)


SIMILAR_K = 3


def _query_vector(incident: dict) -> list[float]:
    vector = incident.get("fingerprint")
    if is_valid(vector):
        return vector
    # Su co cu chua co fingerprint -> tinh lai tu evidence
    return fingerprint(IncidentEvidence(**(incident.get("evidence") or {})))


def similar_incidents(incident_id: str, k: int = SIMILAR_K) -> dict:
    """Su co da hoi phuc giong nhat (Vector Search tren fingerprint) va cach da xu ly."""
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    return _find_similar(incident, k)


def _find_similar(incident: dict, k: int) -> dict:
    pond_id = incident.get("pond_id")
    matches, mode = incidents_repo.find_similar(_query_vector(incident), incident["_id"], k)
    top = matches[0] if matches else None
    top_score = round(top["score"], 4) if top else 0.0
    set_span_tags(pond_id=pond_id, similarity_mode=mode, similarity_top_score=top_score)
    tags = {"pond": pond_id, "mode": mode, "root_cause": top.get("root_cause") if top else "none"}
    gauge("incident.similarity_score", top_score, tags=tags)
    increment("incident.similarity_search", tags=tags)
    logger.info(
        "similar incidents found",
        extra={"incident_id": incident["_id"], "pond": pond_id, "mode": mode, "matches": len(matches)},
    )
    return {"incident_id": incident["_id"], "mode": mode, "matches": matches}


def _attach_similar(incident: dict) -> None:
    """Buoc Dieu tra: luu goi y tu su co cu vao incident va events. Loi tim kiem khong chan chuyen trang thai."""
    try:
        result = _find_similar(incident, SIMILAR_K)
    except Exception as e:
        logger.warning("similar incident lookup failed", extra={"incident_id": incident["_id"], "err": str(e)})
        return
    if not result["matches"]:
        return
    summary = [
        {
            "incident_id": m["_id"],
            "score": round(m["score"], 4),
            "root_cause": m.get("root_cause"),
            "recovery_minutes": m.get("recovery_minutes"),
            "actions": m.get("actions", []),
        }
        for m in result["matches"]
    ]
    incidents_repo.update_fields(incident["_id"], {"similar_incidents": summary})
    events_repo.record(
        "incident_similar_matched",
        incident.get("pond_id"),
        incident_id=incident["_id"],
        details={
            "mode": result["mode"],
            "matches": [{"incident_id": m["incident_id"], "score": m["score"]} for m in summary],
        },
    )


def import_history(incidents: list[Incident]) -> int:
    """Nap lich su su co da hoi phuc (du lieu tong hop) lam co so cho Vector Search. Idempotent theo _id."""
    imported = 0
    for incident in incidents:
        if incidents_repo.insert_if_absent(_to_doc(incident)):
            events_repo.record(
                "incident_imported",
                incident.pond_id,
                incident_id=incident.id,
                details={"state": incident.state.value, "root_cause": incident.root_cause},
            )
            imported += 1
    logger.info("incident history imported", extra={"imported": imported, "total": len(incidents)})
    return imported


def list_incidents(state: IncidentState | None = None) -> list[dict]:
    return incidents_repo.list_recent(state)


def timeline(incident_id: str) -> dict:
    row = incidents_repo.timeline(incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    set_span_tags(pond_id=row.get("pond_id"))
    return row
