"""Start and stop the demo system faults; every change is written to `events` and sent as a Datadog Event."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.log import get_logger
from app.core.telemetry import event
from app.db.repositories import events as events_repo
from app.db.repositories import system_faults as faults_repo
from app.domain.system_faults import FAULTS, FaultType, is_active

logger = get_logger("services.system_faults")


def active(now: datetime | None = None) -> set[FaultType]:
    now = now or utcnow()
    return {FaultType(d["_id"]) for d in faults_repo.list_all() if d["_id"] in set(FaultType) and is_active(d, now)}


# The API checks for injected errors on every request: read MongoDB at most this often
_CACHE_SEC = 5.0
_cache: tuple[float, set[FaultType]] = (float("-inf"), set())


def active_cached() -> set[FaultType]:
    global _cache
    checked_at, faults = _cache
    if time.monotonic() - checked_at >= _CACHE_SEC:
        try:
            faults = active()
        except Exception as e:
            # An unreachable MongoDB must not turn into injected errors, nor hide the real ones
            logger.warning("system faults unreadable", extra={"err": str(e)})
            faults = set()
        _cache = (time.monotonic(), faults)
    return faults


def _forget_cache() -> None:
    global _cache
    _cache = (float("-inf"), set())


def status(now: datetime | None = None) -> list[dict]:
    """Every fault with its text and, when on, until when (for the control panel)."""
    now = now or utcnow()
    stored = {d["_id"]: d for d in faults_repo.list_all()}
    result = []
    for fault, spec in FAULTS.items():
        doc = stored.get(fault.value)
        on = is_active(doc, now)
        result.append(
            {
                "type": fault.value,
                "label": spec.label,
                "description": spec.description,
                "watch": spec.watch,
                "duration_min": spec.duration_min,
                "active": on,
                "until": doc["until"] if on else None,
                "remaining_sec": round((doc["until"] - now).total_seconds()) if on else 0,
            }
        )
    return result


def start(fault: FaultType) -> dict:
    """Switch a fault on for its duration (restarting the clock if it is already on)."""
    spec = FAULTS[fault]
    now = utcnow()
    until = now + timedelta(minutes=spec.duration_min)
    faults_repo.set_fault(fault.value, now, until)
    _forget_cache()
    events_repo.record("system_fault_start", None, details={"fault": fault.value, "until": until})
    event(
        f"[ShrimpWatch] Sự cố hệ thống bật: {spec.label}",
        f"{spec.description}. Xem: {spec.watch}. Tự tắt sau {spec.duration_min} phút.",
        alert_type="warning",
        aggregation_key=f"system_fault-{fault.value}",
        tags={"system_fault": fault.value},
    )
    logger.warning("system fault started", extra={"fault": fault.value, "until": until.isoformat()})
    return {"type": fault.value, "active": True, "until": until}


def clear(fault: FaultType | None = None) -> list[str]:
    """Switch one fault off, or all of them. Returns the ones that were stored."""
    cleared = [f.value for f in ([fault] if fault else list(FaultType)) if faults_repo.clear(f.value)]
    _forget_cache()
    for name in cleared:
        events_repo.record("system_fault_clear", None, details={"fault": name})
        event(
            f"[ShrimpWatch] Sự cố hệ thống tắt: {FAULTS[FaultType(name)].label}",
            "Tắt từ bảng điều khiển.",
            alert_type="success",
            aggregation_key=f"system_fault-{name}",
            tags={"system_fault": name},
        )
        logger.info("system fault cleared", extra={"fault": name})
    return cleared
