from fastapi import APIRouter

from app.core.datadog_links import dashboard_url
from app.domain.system_faults import FaultType
from app.services import system_faults as fault_service

router = APIRouter(prefix="/system-faults", tags=["system faults"])


@router.get("")
def list_faults():
    """The demo faults, which are on, and the Datadog dashboard where their effect shows."""
    return {"faults": fault_service.status(), "dashboard": dashboard_url()}


@router.post("/{fault}")
def start_fault(fault: FaultType):
    """Break ShrimpWatch on purpose for a few minutes (it switches itself off)."""
    return fault_service.start(fault)


@router.delete("/{fault}")
def clear_fault(fault: FaultType):
    return {"cleared": fault_service.clear(fault)}


@router.delete("")
def clear_all_faults():
    return {"cleared": fault_service.clear()}
