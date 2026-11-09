from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.telemetry import increment, set_span_tags
from app.db.repositories import events as events_repo
from app.db.repositories import metrics as metrics_repo
from app.db.repositories import ponds as ponds_repo
from app.domain.conditions import (
    CONDITIONS,
    ConditionType,
    SEVERITY_FACTOR,
    REMEDIES,
    Remedy,
    Severity,
    active_conditions,
    applicable_remedies,
    at_full_intensity,
    build_condition,
    conflicts_with,
    metric_changes,
)
from app.domain.models import MANUAL_METRIC_UNITS, DeviceType, MetricType
from app.domain.ponds import pond_ids
from app.domain.scenarios import SCENARIO_LIMITS, ScenarioType, build_scenario, validate_offset

# Sensor metrics shown on the control panel (sent by the simulator every 30 s)
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
# Test-kit readings are entered a few times a day (the simulator: every 6 h), so look back a full day
MANUAL_WINDOW = timedelta(hours=24)
# The simulator looks back up to 6h to know where to start backfilling
MAX_READINGS_WINDOW_MIN = 360


class PondNotFound(LookupError):
    pass


def list_ponds() -> list[dict]:
    """Pond documents for the control panel, with what the running conditions do.

    `conditions` keeps only the ones still running (one-off actions expire), `active` gives each one's severity and
    progress (0..1), `changes` the metrics they are heading towards and `remedies` the fixes that apply.
    """
    ponds = ponds_repo.list_all()
    now = utcnow()
    for pond in ponds:
        active = active_conditions(pond.get("conditions"), now)
        if "conditions" in pond:
            if active:
                pond["conditions"] = {a.type.value: pond["conditions"][a.type.value] for a in active}
            else:
                del pond["conditions"]
        pond["active"] = [
            {"type": a.type.value, "severity": a.severity.value, "intensity": round(a.intensity, 2)} for a in active
        ]
        pond["changes"] = metric_changes(at_full_intensity(active))
        pond["remedies"] = [r.value for r in applicable_remedies(active)]
    return ponds


def set_pump_failed(pond_id: str, failed: bool) -> None:
    """Mark the pump as failed/repaired (the simulator reads this to simulate a DO drop)."""
    set_span_tags(pond_id=pond_id, device_type=DeviceType.PUMP.value)
    if not ponds_repo.set_pump_failed(pond_id, failed):
        raise PondNotFound(pond_id)
    events_repo.record("pump_state_change", pond_id, details={"failed": failed})
    increment("pump.failure", tags={"pond": pond_id, "state": "fail" if failed else "ok"})


def start_scenario(pond_id: str, scenario: ScenarioType, offset: float, duration_min: int) -> dict:
    """Start a metric-offset scenario; overwrites a running scenario of the same type. InvalidScenario if over the limit."""
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


def start_condition(pond_id: str, condition: ConditionType, severity: Severity = Severity.MEDIUM) -> dict:
    """Turn a condition on until it is cleared (or, for one-off actions, until it fades); several can run at once,
    except ones that contradict it (heavy rain vs hot sun...): those are ended."""
    set_span_tags(pond_id=pond_id, scenario=condition.value)
    doc = build_condition(utcnow(), severity)
    if not ponds_repo.set_condition(pond_id, condition.value, doc):
        raise PondNotFound(pond_id)
    for other in conflicts_with(condition):
        ponds_repo.clear_conditions(pond_id, other.value)
    events_repo.record("condition_start", pond_id, details={"condition": condition.value, "severity": severity.value})
    increment("scenario.change", tags={"pond": pond_id, "scenario": condition.value, "state": "start"})
    return doc


def clear_conditions(pond_id: str, condition: ConditionType | None = None) -> None:
    name = condition.value if condition else "all"
    set_span_tags(pond_id=pond_id, scenario=name)
    if not ponds_repo.clear_conditions(pond_id, condition.value if condition else None):
        raise PondNotFound(pond_id)
    events_repo.record("condition_clear", pond_id, details={"condition": name})
    increment("scenario.change", tags={"pond": pond_id, "scenario": name, "state": "clear"})


def apply_remedy(pond_id: str, remedy: Remedy) -> list[str]:
    """A farmer's fix: ends the conditions it solves. Returns the ones that were running."""
    set_span_tags(pond_id=pond_id, scenario=remedy.value)
    pond = next((p for p in ponds_repo.list_all() if p["_id"] == pond_id), None)
    if pond is None:
        raise PondNotFound(pond_id)
    running = {a.type for a in active_conditions(pond.get("conditions"), utcnow())}
    cleared = [c for c in REMEDIES[remedy].clears if c in running]
    for condition in cleared:
        ponds_repo.clear_conditions(pond_id, condition.value)
        increment("scenario.change", tags={"pond": pond_id, "scenario": condition.value, "state": "clear"})
    # The aerator can also be stopped by the older pump flag (POST /ponds/{id}/pump/fail, `make demo`)
    if remedy == Remedy.REPAIR_AERATOR and pond.get("pump", {}).get("failed"):
        set_pump_failed(pond_id, False)
    events_repo.record(
        "remedy_applied", pond_id, details={"remedy": remedy.value, "cleared": [c.value for c in cleared]}
    )
    return [c.value for c in cleared]


def scenario_catalog() -> dict:
    """Scenario limits and alert thresholds, so the UI does not have to duplicate the config."""
    return {
        "conditions": [
            {
                "type": c.value,
                "label": cond.label,
                "description": cond.description,
                "changes": metric_changes([c]),
                "conflicts": [o.value for o in conflicts_with(c)],
                "ramp_min": cond.ramp_min,
                "duration_min": cond.duration_min,
            }
            for c, cond in CONDITIONS.items()
        ],
        "severities": [{"type": s.value, "factor": f} for s, f in SEVERITY_FACTOR.items()],
        "remedies": [
            {"type": r.value, "label": info.label, "description": info.description} for r, info in REMEDIES.items()
        ],
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
            "alk_warn_low": settings.ALK_WARN,
            "nitrite_warn": settings.NITRITE_WARN,
            "tan_warn": settings.TAN_WARN,
            "pump_failed_max_watts": settings.PUMP_FAILED_MAX_WATTS,
            "detection_slo_sec": settings.DETECTION_SLO_SEC,
            "remedy_check_sec": settings.REMEDY_CHECK_SEC,
        },
    }


def live_readings(window: timedelta = LIVE_WINDOW) -> dict[str, dict[str, dict]]:
    """pond_id -> metric -> {value, unit, timestamp}: the latest value within the recent `window` (default 10 minutes)."""
    return _latest_by_pond([m.value for m in LIVE_METRICS], window)


def manual_readings(window: timedelta = MANUAL_WINDOW) -> dict[str, dict[str, dict]]:
    """Same shape as `live_readings`, for test-kit metrics (alkalinity, TAN, nitrite) within the last 24 hours.

    Kept apart from `live_readings`: the simulator takes the newest timestamp there as the end of the sensor series.
    """
    return _latest_by_pond([m.value for m in MANUAL_METRIC_UNITS], window)


def _latest_by_pond(metrics: list[str], window: timedelta) -> dict[str, dict[str, dict]]:
    rows = metrics_repo.latest_readings(pond_ids(), metrics, utcnow() - window)
    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        result.setdefault(row["pond_id"], {})[row["metric"]] = {
            "value": row["value"],
            "unit": row.get("unit"),
            "timestamp": row["timestamp"],
        }
    return result
