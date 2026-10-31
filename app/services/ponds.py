from __future__ import annotations

from app.core.telemetry import increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import ponds as ponds_repo
from app.domain.models import DeviceType


class PondNotFound(LookupError):
    pass


def list_ponds() -> list[dict]:
    return ponds_repo.list_all()


def set_pump_failed(pond_id: str, failed: bool) -> None:
    """Danh dau quat hong/da sua (simulator doc trang thai nay de mo phong DO sut)."""
    set_span_tags(pond_id=pond_id, device_type=DeviceType.PUMP.value)
    found = ponds_repo.set_pump_failed(pond_id, failed)
    events_repo.record("pump_state_change", pond_id, details={"failed": failed})
    increment("pump.failure", tags={"pond": pond_id, "state": "fail" if failed else "ok"})
    if not found:
        raise PondNotFound(pond_id)
