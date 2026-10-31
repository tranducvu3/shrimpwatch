from fastapi import APIRouter, HTTPException

from app.services import ponds as pond_service

router = APIRouter(prefix="/ponds", tags=["ponds"])


@router.get("")
def list_ponds():
    return pond_service.list_ponds()


@router.post("/{pond_id}/pump/fail")
def mark_pump_failed(pond_id: str, failed: bool = True):
    try:
        pond_service.set_pump_failed(pond_id, failed)
    except pond_service.PondNotFound as e:
        raise HTTPException(404, "pond not found") from e
    return {"pond_id": pond_id, "pump_failed": failed}
