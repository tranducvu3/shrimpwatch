"""Did the operator's remedy work? Pure: no DB, no HTTP.

A remedy is judged on the metric behind the incident's root cause: its value when the remedy was applied (the
baseline) against its value at least REMEDY_CHECK_SEC later. Moving towards normal by more than sensor noise means
it is working (the worker closes the incident once the pond is stable); staying the same or getting worse means it
failed, and the operator investigates again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.domain.incident_rules import Thresholds
from app.domain.models import MetricType


class RemedyOutcome(str, Enum):
    PENDING = "pending"  # too early, or no reading to compare yet
    IMPROVING = "improving"
    FAILED = "failed"


@dataclass(frozen=True)
class _Watch:
    # How bad a value is: higher is worse
    badness: Callable[[float, Thresholds], float]
    # Smallest improvement that is not sensor noise, in the metric's unit
    tolerance: float


def _low_is_bad(value: float, _t: Thresholds) -> float:
    return -value


def _high_is_bad(value: float, _t: Thresholds) -> float:
    return value


def _temperature_off_centre(value: float, t: Thresholds) -> float:
    return abs(value - (t.temp_warn_low + t.temp_warn_high) / 2)


def _ph_off_centre(value: float, t: Thresholds) -> float:
    return abs(value - (t.ph_warn_low + t.ph_warn_high) / 2)


_WATCHES: dict[MetricType, _Watch] = {
    MetricType.DO: _Watch(_low_is_bad, 0.05),
    MetricType.ORP: _Watch(_low_is_bad, 10.0),
    MetricType.NH3: _Watch(_high_is_bad, 0.005),
    MetricType.CO2: _Watch(_high_is_bad, 0.5),
    MetricType.TAN: _Watch(_high_is_bad, 0.05),
    MetricType.NITRITE: _Watch(_high_is_bad, 0.05),
    MetricType.ALKALINITY: _Watch(_low_is_bad, 5.0),
    MetricType.SALINITY: _Watch(_low_is_bad, 0.2),
    MetricType.TEMPERATURE: _Watch(_temperature_off_centre, 0.3),
    MetricType.PH: _Watch(_ph_off_centre, 0.05),
    # An aerator either runs (~1500 W) or not: anything under a few hundred watts is not a repair
    MetricType.PUMP_POWER: _Watch(_low_is_bad, 300.0),
}

_ROOT_CAUSE_METRIC: dict[str, MetricType] = {
    # Judged on the aerator itself: DO lags, and after a short stop it may never have left the safe range,
    # so "DO barely rose" would call a working repair a failure
    "pump_failure": MetricType.PUMP_POWER,
    "do_out_of_range": MetricType.DO,
    "orp_out_of_range": MetricType.ORP,
    "nh3_out_of_range": MetricType.NH3,
    "co2_out_of_range": MetricType.CO2,
    "tan_out_of_range": MetricType.TAN,
    "nitrite_out_of_range": MetricType.NITRITE,
    "alkalinity_out_of_range": MetricType.ALKALINITY,
    "salinity_out_of_range": MetricType.SALINITY,
    "temperature_out_of_range": MetricType.TEMPERATURE,
    "ph_out_of_range": MetricType.PH,
}


def watched_metric(root_cause: str | None) -> MetricType | None:
    """The metric a remedy for this root cause should move; None for an unknown root cause."""
    return _ROOT_CAUSE_METRIC.get(root_cause or "")


def remedy_outcome(
    metric: MetricType | None,
    baseline: float | None,
    current: float | None,
    elapsed_sec: float,
    wait_sec: float,
    thresholds: Thresholds,
    still_abnormal: bool = True,
) -> RemedyOutcome:
    """IMPROVING as soon as the pond no longer breaks any rule (`still_abnormal` False): its metrics are back to
    normal, even if the root-cause metric has barely moved yet. Otherwise PENDING before `wait_sec` or without both
    readings, then IMPROVING or FAILED by how far the metric moved."""
    if not still_abnormal:
        return RemedyOutcome.IMPROVING
    watch = _WATCHES.get(metric) if metric else None
    if elapsed_sec < wait_sec or watch is None or baseline is None or current is None:
        return RemedyOutcome.PENDING
    gain = watch.badness(baseline, thresholds) - watch.badness(current, thresholds)
    return RemedyOutcome.IMPROVING if gain >= watch.tolerance else RemedyOutcome.FAILED
