from fastapi import APIRouter, HTTPException

from app.api.schemas import ScenarioRequest
from app.domain.scenarios import InvalidScenario, ScenarioType
from app.services import ponds as pond_service

router = APIRouter(prefix="/ponds", tags=["ponds"])


@router.get("")
def list_ponds():
    return pond_service.list_ponds()


@router.get("/readings")
def live_readings():
    """Gia tri cam bien moi nhat moi ao (10 phut gan day)."""
    return pond_service.live_readings()


@router.get("/scenarios/catalog")
def scenario_catalog():
    return pond_service.scenario_catalog()


@router.post("/{pond_id}/pump/fail")
def mark_pump_failed(pond_id: str, failed: bool = True):
    try:
        pond_service.set_pump_failed(pond_id, failed)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "pump_failed": failed}


@router.post("/{pond_id}/scenarios")
def start_scenario(pond_id: str, body: ScenarioRequest):
    try:
        doc = pond_service.start_scenario(pond_id, body.scenario, body.offset, body.duration_min)
    except InvalidScenario as e:
        raise HTTPException(422, str(e)) from e
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "scenario": body.scenario.value, **doc}


@router.delete("/{pond_id}/scenarios")
def clear_all_scenarios(pond_id: str):
    try:
        pond_service.clear_scenarios(pond_id)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "cleared": "all"}


@router.delete("/{pond_id}/scenarios/{scenario}")
def clear_scenario(pond_id: str, scenario: ScenarioType):
    try:
        pond_service.clear_scenarios(pond_id, scenario)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "cleared": scenario.value}
