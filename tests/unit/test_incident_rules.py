import pytest

from app.domain.incident_rules import PondSnapshot, Thresholds, evaluate, has_recovered
from app.domain.models import Severity

T = Thresholds(
    do_warn=4.0,
    do_critical=3.0,
    do_drop_rate_warn=0.05,
    pump_failed_max_watts=50.0,
    pump_running_min_watts=500.0,
    do_recovery_margin=0.5,
)


def snap(avg_pump=1500.0, drop_rate=0.0, min_do=6.0):
    return PondSnapshot("pond-01", avg_pump_watts=avg_pump, do_drop_rate=drop_rate, min_do=min_do)


def test_healthy_pond_has_no_finding():
    assert evaluate(snap(), T) is None


def test_pump_failure_is_critical():
    f = evaluate(snap(avg_pump=10.0, drop_rate=0.1, min_do=4.5), T)
    assert (f.severity, f.root_cause) == (Severity.CRITICAL, "pump_failure")


def test_pump_failure_without_do_window_does_not_crash():
    f = evaluate(snap(avg_pump=0.0, drop_rate=None, min_do=None), T)
    assert f.root_cause == "pump_failure"
    assert "n/a" in f.description


def test_pump_failure_while_do_rising_falls_through_to_do_rules():
    f = evaluate(snap(avg_pump=10.0, drop_rate=-0.1, min_do=2.5), T)
    assert f.root_cause == "do_critical"


def test_fast_do_drop_with_running_pump_is_warning():
    f = evaluate(snap(drop_rate=0.08, min_do=5.0), T)
    assert (f.severity, f.root_cause) == (Severity.WARNING, "do_anomaly_drop")


@pytest.mark.parametrize(("min_do", "root_cause"), [(2.9, "do_critical"), (3.9, "do_low")])
def test_low_do_thresholds(min_do, root_cause):
    assert evaluate(snap(min_do=min_do), T).root_cause == root_cause


def test_recovery_needs_safe_do_and_running_pump():
    assert has_recovered(snap(min_do=4.6), T)
    assert has_recovered(snap(avg_pump=None, min_do=4.6), T)
    assert not has_recovered(snap(min_do=4.4), T)
    assert not has_recovered(snap(avg_pump=200.0, min_do=6.0), T)
    assert not has_recovered(snap(min_do=None), T)
