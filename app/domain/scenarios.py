"""Kich ban mo phong bat thuong: lech chi so moi truong so voi baseline trong mot khoang thoi gian.

Ham thuan: API ghi kich ban vao document ao, simulator doc lai va cong do lech vao chi so cam bien.
Tat quat dung co `pump.failed` rieng (POST /ponds/{id}/pump/fail), khong nam o day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from app.core.clock import to_naive_utc


class ScenarioType(str, Enum):
    DO = "do"
    TEMPERATURE = "temperature"
    PH = "ph"
    SALINITY = "salinity"


@dataclass(frozen=True)
class ScenarioLimit:
    min_offset: float
    max_offset: float
    unit: str
    label: str


# Bien do lech cho phep: du lon de vuot nguong canh bao, khong lon den muc phi vat ly.
SCENARIO_LIMITS: dict[ScenarioType, ScenarioLimit] = {
    ScenarioType.DO: ScenarioLimit(-5.0, 2.0, "mg/L", "Oxy hòa tan (DO)"),
    ScenarioType.TEMPERATURE: ScenarioLimit(-6.0, 6.0, "C", "Nhiệt độ"),
    ScenarioType.PH: ScenarioLimit(-1.5, 1.5, "pH", "pH"),
    ScenarioType.SALINITY: ScenarioLimit(-10.0, 10.0, "ppt", "Độ mặn"),
}

MAX_DURATION_MIN = 720


class InvalidScenario(ValueError):
    pass


def validate_offset(scenario: ScenarioType, offset: float) -> None:
    limit = SCENARIO_LIMITS[scenario]
    if not limit.min_offset <= offset <= limit.max_offset:
        raise InvalidScenario(
            f"{scenario.value} offset must be between {limit.min_offset} and {limit.max_offset} {limit.unit}"
        )


def build_scenario(offset: float, duration_min: int, now: datetime) -> dict[str, Any]:
    """Document luu trong `ponds.scenarios.<type>`."""
    return {"offset": offset, "started_at": now, "until": now + timedelta(minutes=duration_min)}


def _as_datetime(value: Any) -> datetime | None:
    # API tra JSON nen simulator nhan chuoi ISO; trong service la datetime
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, str):
        try:
            return to_naive_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def active_offsets(scenarios: dict[str, Any] | None, now: datetime) -> dict[ScenarioType, float]:
    """Do lech dang hieu luc tai `now`; kich ban het han hoac sai dinh dang bi bo qua."""
    result: dict[ScenarioType, float] = {}
    for key, doc in (scenarios or {}).items():
        try:
            scenario = ScenarioType(key)
        except ValueError:
            continue
        if not isinstance(doc, dict):
            continue
        until = _as_datetime(doc.get("until"))
        offset = doc.get("offset")
        if until is None or until <= now or not isinstance(offset, int | float):
            continue
        result[scenario] = float(offset)
    return result
