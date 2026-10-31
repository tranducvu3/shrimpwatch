from fastapi import APIRouter, HTTPException

from app.domain.models import IncidentState
from app.services import incidents as incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
def list_incidents(state: IncidentState | None = None):
    return incident_service.list_incidents(state)


@router.get("/{incident_id}/timeline")
def incident_timeline(incident_id: str):
    try:
        return incident_service.timeline(incident_id)
    except incident_service.IncidentNotFound as e:
        raise HTTPException(404, "incident not found") from e


@router.patch("/{incident_id}")
def patch_incident(incident_id: str, state: IncidentState, action: str | None = None):
    try:
        incident_service.transition(incident_id, state, action)
    except incident_service.IncidentNotFound as e:
        raise HTTPException(404, "incident not found") from e
    return {"ok": True, "incident_id": incident_id, "state": state.value}
