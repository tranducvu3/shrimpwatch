"""Pond conditions: real-world situations (heavy rain, overfeeding, aerator failure...) that change several metrics.

Each condition is a bundle of offsets on the base metrics, scaled by a severity (light/medium/severe). The effect
builds up over `ramp_min` minutes; a condition with `duration_min` (liming, water exchange) fades out linearly and
expires on its own. Several conditions can run at once on a pond: their offsets add up, clamped to the scenario
limits so the result stays physical. Remedies (repair the aerator, clean the bottom...) end the conditions they fix.
Pure functions: the API stores the active conditions in the pond document, the simulator reads them back and
applies `combined_effects`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.domain.models import MetricType
from app.domain.scenarios import SCENARIO_LIMITS, ScenarioType
from app.domain.water_chemistry import co2_mg_per_l, nh3_mg_per_l


class ConditionType(str, Enum):
    HEAVY_RAIN = "heavy_rain"
    HOT_SUN = "hot_sun"
    OVERFEEDING = "overfeeding"
    ALGAE_BLOOM = "algae_bloom"
    AERATOR_FAILURE = "aerator_failure"
    CLOUDY = "cloudy"
    HIGH_DENSITY = "high_density"
    WATER_EXCHANGE = "water_exchange"
    ALGAE_CRASH = "algae_crash"
    DIRTY_BOTTOM = "dirty_bottom"
    SALINITY_RISE = "salinity_rise"
    LIMING = "liming"
    SENSOR_OFFLINE = "sensor_offline"


class Severity(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    SEVERE = "severe"


# Multiplier on the condition's offsets: the `effects` below are the medium case
SEVERITY_FACTOR = {Severity.LIGHT: 0.5, Severity.MEDIUM: 1.0, Severity.SEVERE: 1.5}
DEFAULT_RAMP_MIN = 15


@dataclass(frozen=True)
class Condition:
    label: str
    description: str
    # Offset added to each metric's baseline (sensor: do/temperature/ph/salinity; test kit: tan/nitrite/alkalinity)
    effects: dict[MetricType, float] = field(default_factory=dict)
    # Stops the aerator: DO falls steadily, ORP follows, pump power drops to 0
    pump_failed: bool = False
    # The water-quality probe stops sending (DO, temperature, pH, salinity, ORP); the pond itself does not change
    sensor_offline: bool = False
    # Minutes for the effect to build up from 0 to full (0 = at once)
    ramp_min: int = DEFAULT_RAMP_MIN
    # One-off actions (liming, water exchange) fade out linearly over this many minutes, then end; None = until cleared
    duration_min: int | None = None


CONDITIONS: dict[ConditionType, Condition] = {
    ConditionType.HEAVY_RAIN: Condition(
        "Mưa lớn",
        "Nước mưa pha loãng ao: giảm độ mặn, nhiệt độ, pH và độ kiềm",
        {MetricType.SALINITY: -6.0, MetricType.TEMPERATURE: -2.0, MetricType.PH: -0.4, MetricType.ALKALINITY: -30.0},
        ramp_min=5,
    ),
    ConditionType.HOT_SUN: Condition(
        "Nắng nóng",
        "Nước nóng lên, giữ được ít oxy hơn; tảo quang hợp làm pH tăng",
        {MetricType.TEMPERATURE: 4.0, MetricType.DO: -1.0, MetricType.PH: 0.3},
        ramp_min=20,
    ),
    ConditionType.OVERFEEDING: Condition(
        "Cho ăn dư",
        "Thức ăn thừa phân huỷ: tiêu tốn oxy, tăng amoni (TAN) và nitrite",
        {MetricType.DO: -1.5, MetricType.PH: -0.2, MetricType.TAN: 1.2, MetricType.NITRITE: 0.6},
        ramp_min=15,
    ),
    ConditionType.ALGAE_BLOOM: Condition(
        "Tảo phát triển",
        "Tảo dày quang hợp mạnh ban ngày: pH và DO tăng cao",
        {MetricType.PH: 0.6, MetricType.DO: 1.0},
        ramp_min=30,
    ),
    ConditionType.AERATOR_FAILURE: Condition(
        "Quạt nước hỏng",
        "Quạt ngừng chạy: DO và ORP giảm dần, công suất quạt về 0",
        pump_failed=True,
        ramp_min=0,
    ),
    ConditionType.CLOUDY: Condition(
        "Trời âm u",
        "Ít nắng, tảo quang hợp kém: DO và pH giảm, nước mát hơn",
        {MetricType.DO: -1.5, MetricType.PH: -0.3, MetricType.TEMPERATURE: -1.0},
        ramp_min=20,
    ),
    ConditionType.HIGH_DENSITY: Condition(
        "Mật độ nuôi cao",
        "Nhiều tôm hô hấp và thải hơn: DO giảm, TAN và nitrite tăng",
        {MetricType.DO: -1.5, MetricType.PH: -0.15, MetricType.TAN: 0.6, MetricType.NITRITE: 0.2},
        ramp_min=30,
    ),
    ConditionType.WATER_EXCHANGE: Condition(
        "Thay nước",
        "Thay một phần nước mới: giảm TAN, nitrite, độ kiềm; DO tăng nhẹ",
        {
            MetricType.TEMPERATURE: -1.0,
            MetricType.DO: 0.5,
            MetricType.TAN: -0.3,
            MetricType.NITRITE: -0.15,
            MetricType.ALKALINITY: -15.0,
        },
        ramp_min=0,
        duration_min=120,
    ),
    ConditionType.ALGAE_CRASH: Condition(
        "Tảo tàn",
        "Tảo chết hàng loạt: phân huỷ tiêu tốn oxy, pH giảm mạnh, amoni (TAN) và nitrite tăng vọt",
        {MetricType.DO: -2.5, MetricType.PH: -0.5, MetricType.TAN: 1.5, MetricType.NITRITE: 0.5},
        ramp_min=20,
    ),
    ConditionType.DIRTY_BOTTOM: Condition(
        "Đáy ao bẩn",
        "Bùn đáy tích tụ phân huỷ: DO giảm, pH giảm, TAN và nitrite tăng",
        {MetricType.DO: -1.2, MetricType.PH: -0.25, MetricType.TAN: 0.9, MetricType.NITRITE: 0.3},
        ramp_min=30,
    ),
    ConditionType.SALINITY_RISE: Condition(
        "Độ mặn tăng",
        "Nắng hạn, bốc hơi hoặc xâm nhập mặn: độ mặn và nhiệt độ tăng, DO giảm nhẹ",
        {MetricType.SALINITY: 6.0, MetricType.TEMPERATURE: 1.0, MetricType.DO: -0.5},
        ramp_min=30,
    ),
    ConditionType.LIMING: Condition(
        "Bón vôi",
        "Rải vôi nâng độ kiềm và pH, kéo CO2 xuống",
        {MetricType.PH: 0.5, MetricType.ALKALINITY: 40.0},
        ramp_min=0,
        duration_min=240,
    ),
    ConditionType.SENSOR_OFFLINE: Condition(
        "Đầu dò mất kết nối",
        "Đầu dò đo nước hết pin hoặc đứt dây: không còn số đo DO, pH, nhiệt độ, độ mặn, ORP",
        sensor_offline=True,
        ramp_min=0,
    ),
}

# Sensor metrics the simulator offsets directly; the rest of `effects` are test-kit metrics
SENSOR_SCENARIOS: dict[MetricType, ScenarioType] = {
    MetricType.DO: ScenarioType.DO,
    MetricType.TEMPERATURE: ScenarioType.TEMPERATURE,
    MetricType.PH: ScenarioType.PH,
    MetricType.SALINITY: ScenarioType.SALINITY,
}

# Typical healthy pond, only used to tell which way the derived metrics (NH3, CO2) move
_TYPICAL = {MetricType.TAN: 0.6, MetricType.PH: 8.2, MetricType.TEMPERATURE: 29.5, MetricType.ALKALINITY: 150.0}


@dataclass(frozen=True)
class Effects:
    sensor_offsets: dict[ScenarioType, float]
    manual_offsets: dict[MetricType, float]
    pump_failed: bool
    sensor_offline: bool = False


class Remedy(str, Enum):
    """A farmer's response: ends the conditions it fixes (liming and water exchange are conditions themselves)."""

    REPAIR_AERATOR = "repair_aerator"
    CLEAN_BOTTOM = "clean_bottom"
    CUT_FEED = "cut_feed"
    THIN_STOCK = "thin_stock"
    LIME = "lime"
    FIX_SENSOR = "fix_sensor"
    BACKUP_AERATOR = "backup_aerator"


@dataclass(frozen=True)
class RemedyInfo:
    label: str
    description: str
    clears: tuple[ConditionType, ...]


REMEDIES: dict[Remedy, RemedyInfo] = {
    Remedy.REPAIR_AERATOR: RemedyInfo("Sửa quạt nước", "Quạt chạy lại", (ConditionType.AERATOR_FAILURE,)),
    Remedy.CLEAN_BOTTOM: RemedyInfo("Hút bùn đáy", "Siphon bùn, dọn đáy ao", (ConditionType.DIRTY_BOTTOM,)),
    Remedy.CUT_FEED: RemedyInfo("Ngừng cho ăn", "Dừng cho ăn, vớt thức ăn thừa", (ConditionType.OVERFEEDING,)),
    Remedy.THIN_STOCK: RemedyInfo("Thu tỉa tôm", "Giảm mật độ nuôi", (ConditionType.HIGH_DENSITY,)),
    # Simplified for the demo: liming undoes the whole dilution (rain, water exchange) at once instead of the
    # slow real-world recovery, so the farmer sees the remedy work in the next test-kit reading
    Remedy.LIME: RemedyInfo(
        "Bón vôi",
        "Rải vôi nâng độ kiềm và pH bị mưa hoặc thay nước pha loãng",
        (ConditionType.HEAVY_RAIN, ConditionType.WATER_EXCHANGE),
    ),
    Remedy.FIX_SENSOR: RemedyInfo(
        "Sửa đầu dò", "Thay pin, nối lại dây hoặc thay đầu dò đo nước", (ConditionType.SENSOR_OFFLINE,)
    ),
    # Switched on remotely through the aerator's relay, so Datadog can apply it without a person (see
    # domain.remedies.automatic_remedy); the main aerator still has to be repaired
    Remedy.BACKUP_AERATOR: RemedyInfo(
        "Bật quạt dự phòng",
        "Bật quạt dự phòng qua rơ-le từ xa để giữ oxy, trong lúc chờ sửa quạt chính",
        (ConditionType.AERATOR_FAILURE,),
    ),
}


@dataclass(frozen=True)
class ActiveCondition:
    type: ConditionType
    severity: Severity = Severity.MEDIUM
    intensity: float = 1.0  # 0..1: how far the ramp-up (and the fade of one-off actions) has got

    @property
    def scale(self) -> float:
        return SEVERITY_FACTOR[self.severity] * self.intensity


# Accepted wherever a list of conditions is: a bare type means medium severity at full intensity
ConditionLike = ConditionType | ActiveCondition


def _active(c: ConditionLike) -> ActiveCondition:
    return c if isinstance(c, ActiveCondition) else ActiveCondition(c)


def build_condition(now: datetime, severity: Severity = Severity.MEDIUM) -> dict[str, Any]:
    """Document stored in `ponds.conditions.<type>`; it stays on until it is cleared (or its duration ends)."""
    return {"started_at": now, "severity": severity.value}


def condition_intensity(condition: ConditionType, started_at: datetime | None, now: datetime) -> float:
    """0..1 at `now`: ramps up over `ramp_min`, then (one-off actions only) fades linearly to 0 at `duration_min`."""
    if started_at is None:
        return 1.0
    spec = CONDITIONS[condition]
    elapsed = max(0.0, (now - started_at).total_seconds() / 60)
    ramp = 1.0 if spec.ramp_min <= 0 else min(1.0, elapsed / spec.ramp_min)
    if spec.duration_min is None:
        return ramp
    return ramp * max(0.0, 1.0 - elapsed / spec.duration_min)


def _parse_started(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.removesuffix("Z"))
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


def active_conditions(conditions: dict[str, Any] | None, now: datetime) -> list[ActiveCondition]:
    """Conditions stored on the pond that are still running at `now`; unknown or malformed entries are ignored."""
    result = []
    for key, doc in (conditions or {}).items():
        try:
            condition = ConditionType(key)
        except ValueError:
            continue
        if not isinstance(doc, dict):
            continue
        try:
            severity = Severity(doc.get("severity", Severity.MEDIUM.value))
        except ValueError:
            severity = Severity.MEDIUM
        started = _parse_started(doc.get("started_at"))
        spec = CONDITIONS[condition]
        if spec.duration_min is not None and started and (now - started).total_seconds() >= spec.duration_min * 60:
            continue  # a one-off action that has run its course
        result.append(ActiveCondition(condition, severity, condition_intensity(condition, started, now)))
    return sorted(result, key=lambda a: list(ConditionType).index(a.type))


def at_full_intensity(conditions: list[ConditionLike]) -> list[ActiveCondition]:
    """The same conditions with the ramp ignored: what they are heading towards (for the control panel)."""
    return [ActiveCondition(a.type, a.severity) for a in map(_active, conditions)]


# Situations that cannot be true together: starting one ends the others of its group
EXCLUSIVE_GROUPS: tuple[frozenset[ConditionType], ...] = (
    frozenset({ConditionType.HEAVY_RAIN, ConditionType.HOT_SUN, ConditionType.CLOUDY}),  # the weather right now
    frozenset({ConditionType.HEAVY_RAIN, ConditionType.SALINITY_RISE}),  # rain dilutes, drought concentrates
    frozenset({ConditionType.ALGAE_BLOOM, ConditionType.ALGAE_CRASH}),  # algae thriving or dying
)


def conflicts_with(condition: ConditionType) -> list[ConditionType]:
    """Conditions that cannot run together with `condition`, in catalog order."""
    clashing = {c for group in EXCLUSIVE_GROUPS if condition in group for c in group} - {condition}
    return [c for c in ConditionType if c in clashing]


def applicable_remedies(conditions: list[ConditionLike]) -> list[Remedy]:
    running = {_active(c).type for c in conditions}
    return [r for r, info in REMEDIES.items() if running & set(info.clears)]


def combined_effects(conditions: list[ConditionLike]) -> Effects:
    """Sum the scaled offsets of several conditions; sensor offsets are clamped to the scenario limits."""
    active = [_active(c) for c in conditions]
    totals: dict[MetricType, float] = {}
    for a in active:
        for metric, offset in CONDITIONS[a.type].effects.items():
            totals[metric] = totals.get(metric, 0.0) + offset * a.scale
    sensor = {}
    for metric, scenario in SENSOR_SCENARIOS.items():
        if metric in totals:
            limit = SCENARIO_LIMITS[scenario]
            sensor[scenario] = round(min(limit.max_offset, max(limit.min_offset, totals[metric])), 3)
    manual = {m: round(v, 3) for m, v in totals.items() if m not in SENSOR_SCENARIOS}
    return Effects(
        sensor,
        manual,
        any(CONDITIONS[a.type].pump_failed and a.intensity > 0 for a in active),
        any(CONDITIONS[a.type].sensor_offline for a in active),
    )


def metric_changes(conditions: list[ConditionLike]) -> list[dict[str, Any]]:
    """What the user will see change: [{metric, direction: "up"|"down", offset|None}], direct effects first.

    Derived metrics are included with offset None: ORP follows DO, pump power drops when the aerator stops,
    NH3 and CO2 are recomputed from a typical pond to know which way they move.
    """
    effects = combined_effects(conditions)
    offsets: dict[MetricType, float] = {
        m: effects.sensor_offsets[s] for m, s in SENSOR_SCENARIOS.items() if s in effects.sensor_offsets
    }
    offsets.update(effects.manual_offsets)
    changes = [
        {"metric": m.value, "direction": "up" if v > 0 else "down", "offset": v} for m, v in offsets.items() if v
    ]

    def derived(metric: MetricType, direction: str | None) -> None:
        if direction:
            changes.append({"metric": metric.value, "direction": direction, "offset": None})

    do_offset = offsets.get(MetricType.DO, 0.0)
    if effects.pump_failed:
        # Without aeration DO keeps falling whatever the other conditions do
        changes = [c for c in changes if c["metric"] != MetricType.DO.value]
        changes.insert(0, {"metric": MetricType.DO.value, "direction": "down", "offset": None})
        derived(MetricType.PUMP_POWER, "down")
    do_down = effects.pump_failed or do_offset < 0
    derived(MetricType.ORP, "down" if do_down else "up" if do_offset > 0 else None)

    after = {m: v + offsets.get(m, 0.0) for m, v in _TYPICAL.items()}
    after[MetricType.TAN] = max(0.05, after[MetricType.TAN])
    derived(
        MetricType.NH3,
        _direction(
            nh3_mg_per_l(_TYPICAL[MetricType.TAN], _TYPICAL[MetricType.PH], _TYPICAL[MetricType.TEMPERATURE]),
            nh3_mg_per_l(after[MetricType.TAN], after[MetricType.PH], after[MetricType.TEMPERATURE]),
        ),
    )
    derived(
        MetricType.CO2,
        _direction(
            co2_mg_per_l(_TYPICAL[MetricType.ALKALINITY], _TYPICAL[MetricType.PH]),
            co2_mg_per_l(after[MetricType.ALKALINITY], after[MetricType.PH]),
        ),
    )
    return changes


def _direction(before: float, after: float) -> str | None:
    if abs(after - before) < 0.01 * max(abs(before), 1e-9):
        return None
    return "up" if after > before else "down"
