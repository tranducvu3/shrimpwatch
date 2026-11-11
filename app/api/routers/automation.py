"""Machine-to-machine actions: the Datadog auto-remediation workflow calls this, not people."""

import hmac

from fastapi import APIRouter, Header, HTTPException

from app.api.schemas import RemediationRequest
from app.core.config import settings
from app.domain.incident_lifecycle import InvalidTransition
from app.services import incidents as incident_service
from app.services import ponds as pond_service

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post("/remediate")
def remediate(body: RemediationRequest, x_automation_token: str = Header("")):
    """Apply the pond's automatic remedy to its active incident (backup aerator for a stopped aerator).

    Needs the shared AUTOMATION_TOKEN in `X-Automation-Token`; disabled (503) when it is not configured. Returns
    `applied`, or `skipped` with the reason (no active incident, nothing automatic for it, already applied).
    """
    if not settings.AUTOMATION_TOKEN:
        raise HTTPException(503, "automation is disabled: AUTOMATION_TOKEN is not set")
    if not hmac.compare_digest(x_automation_token.encode(), settings.AUTOMATION_TOKEN.encode()):
        raise HTTPException(401, "invalid automation token")
    try:
        return incident_service.auto_remediate(body.pond_id, body.run_id)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
