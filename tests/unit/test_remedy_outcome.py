import inspect
import re

import pytest

from app.domain import incident_rules
from app.domain.incident_rules import Thresholds
from app.domain.models import MetricType
from app.domain.remedy_outcome import _ROOT_CAUSE_METRIC, _WATCHES, RemedyOutcome, remedy_outcome, watched_metric

T = Thresholds.from_settings()
WAIT = 60


def outcome(metric, baseline, current, elapsed=WAIT):
    return remedy_outcome(metric, baseline, current, elapsed, WAIT, T)


def test_too_early_is_pending_even_when_worse():
    assert outcome(MetricType.DO, 3.0, 2.0, elapsed=59) == RemedyOutcome.PENDING


@pytest.mark.parametrize("baseline, current", [(None, 3.0), (3.0, None)])
def test_missing_reading_is_pending(baseline, current):
    assert outcome(MetricType.DO, baseline, current) == RemedyOutcome.PENDING


def test_unknown_metric_is_pending():
    assert outcome(None, 3.0, 2.0) == RemedyOutcome.PENDING


def test_do_rising_after_aerator_repair_is_improving():
    assert outcome(MetricType.DO, 2.8, 3.0) == RemedyOutcome.IMPROVING


@pytest.mark.parametrize("current", [2.8, 2.82, 2.6])
def test_do_holding_or_falling_failed(current):
    """Within sensor noise (0.05 mg/L) counts as holding."""
    assert outcome(MetricType.DO, 2.8, current) == RemedyOutcome.FAILED


def test_waste_metrics_improve_when_they_fall():
    assert outcome(MetricType.NH3, 0.20, 0.12) == RemedyOutcome.IMPROVING
    assert outcome(MetricType.TAN, 1.5, 1.5) == RemedyOutcome.FAILED
    assert outcome(MetricType.CO2, 12.0, 13.0) == RemedyOutcome.FAILED


def test_ph_improves_towards_the_middle_of_its_range_from_either_side():
    assert outcome(MetricType.PH, 8.8, 8.5) == RemedyOutcome.IMPROVING
    assert outcome(MetricType.PH, 7.2, 7.5) == RemedyOutcome.IMPROVING
    assert outcome(MetricType.PH, 8.8, 9.0) == RemedyOutcome.FAILED


def test_root_causes_map_to_their_metric():
    assert watched_metric("pump_failure") == MetricType.DO
    assert watched_metric("nh3_out_of_range") == MetricType.NH3
    assert watched_metric(None) is None


def test_every_root_cause_the_rules_emit_is_watched():
    emitted = set(re.findall(r'"(pump_failure|\w+_out_of_range)"', inspect.getsource(incident_rules)))
    assert emitted == set(_ROOT_CAUSE_METRIC)
    assert set(_ROOT_CAUSE_METRIC.values()) <= set(_WATCHES)


def test_back_to_normal_counts_as_working_at_once_even_if_the_metric_barely_moved():
    """Repairing the aerator when DO is still high: no rule fires any more, though DO has not risen yet."""
    normal = remedy_outcome(MetricType.DO, 6.15, 6.10, 10, WAIT, T, still_abnormal=False)
    assert normal == RemedyOutcome.IMPROVING
