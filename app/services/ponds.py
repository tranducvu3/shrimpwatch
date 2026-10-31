from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.telemetry import increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import metrics as metrics_repo
from app.db.repositories import ponds as ponds_repo
from app.domain.models import DeviceType, MetricType
from app.domain.ponds import pond_ids
from app.domain.scenarios import SCENARIO_LIMITS, ScenarioType, build_scenario, validate_offset

# Chi so cam bien hien tren bang dieu khien (do simulator gui moi 30 s)
LIVE_METRICS = [
    MetricType.DO,
    MetricType.TEMPERATURE,
    MetricType.PH,
    MetricType.SALINITY,
    MetricType.ORP,
    MetricType.PUMP_POWER,
    MetricType.CO2,
    MetricType.NH3,
]
LIVE_WINDOW = timedelta(minutes=10)
# Simulator hoi lai toi da 6h de biet can backfill tu dau
MAX_READINGS_WINDOW_MIN = 360


class PondNotFound(LookupError):
    pass


def list_ponds() -> list[dict]:
    return ponds_repo.list_all()


def set_pump_failed(pond_id: str, failed: bool) -> None:
    """Danh dau quat hong/da sua (simulator doc trang thai nay de mo phong DO sut)."""
    set_span_tags(pond_id=pond_id, device_type=DeviceType.PUMP.value)
    if not ponds_repo.set_pump_failed(pond_id, failed):
        raise PondNotFound(pond_id)
    events_repo.record("pump_state_change", pond_id, details={"failed": failed})
    increment("pump.failure", tags={"pond": pond_id, "state": "fail" if failed else "ok"})


def start_scenario(pond_id: str, scenario: ScenarioType, offset: float, duration_min: int) -> dict:
    """Bat kich ban lech chi so; ghi de kich ban cung loai dang chay. InvalidScenario neu vuot gioi han."""
    set_span_tags(pond_id=pond_id, scenario=scenario.value)
    validate_offset(scenario, offset)
    doc = build_scenario(offset, duration_min, utcnow())
    if not ponds_repo.set_scenario(pond_id, scenario.value, doc):
        raise PondNotFound(pond_id)
    events_repo.record(
        "scenario_start", pond_id, details={"scenario": scenario.value, "offset": offset, "until": doc["until"]}
    )
    increment("scenario.change", tags={"pond": pond_id, "scenario": scenario.value, "state": "start"})
    return doc


def clear_scenarios(pond_id: str, scenario: ScenarioType | None = None) -> None:
    key = scenario.value if scenario else None
    name = key or "all"
    set_span_tags(pond_id=pond_id, scenario=name)
    if not ponds_repo.clear_scenarios(pond_id, key):
        raise PondNotFound(pond_id)
    events_repo.record("scenario_clear", pond_id, details={"scenario": name})
    increment("scenario.change", tags={"pond": pond_id, "scenario": name, "state": "clear"})


def scenario_catalog() -> dict:
    """Gioi han kich ban va nguong canh bao, de giao dien khong phai chep lai cau hinh."""
    return {
        "scenarios": [
            {
                "type": s.value,
                "label": limit.label,
                "unit": limit.unit,
                "min_offset": limit.min_offset,
                "max_offset": limit.max_offset,
            }
            for s, limit in SCENARIO_LIMITS.items()
        ],
        "thresholds": {
            "do_warn": settings.DO_WARN,
            "do_critical": settings.DO_CRITICAL,
            "temp_warn_low": settings.TEMP_WARN_LOW,
            "temp_warn_high": settings.TEMP_WARN_HIGH,
            "ph_warn_low": settings.PH_WARN_LOW,
            "ph_warn_high": settings.PH_WARN_HIGH,
            "salinity_warn_low": settings.SALINITY_WARN_LOW,
            "orp_warn_low": settings.ORP_WARN_LOW,
            "co2_warn": settings.CO2_WARN,
            "nh3_warn": settings.NH3_WARN,
            "pump_failed_max_watts": settings.PUMP_FAILED_MAX_WATTS,
        },
    }


def live_readings(window: timedelta = LIVE_WINDOW) -> dict[str, dict[str, dict]]:
    """pond_id -> metric -> {value, unit, timestamp}: gia tri moi nhat trong `window` gan day (mac dinh 10 phut)."""
    rows = metrics_repo.latest_readings(pond_ids(), [m.value for m in LIVE_METRICS], utcnow() - window)
    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        result.setdefault(row["pond_id"], {})[row["metric"]] = {
            "value": row["value"],
            "unit": row.get("unit"),
            "timestamp": row["timestamp"],
        }
    return result
