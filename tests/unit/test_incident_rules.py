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
    temp_warn_low=26.0,
    temp_warn_high=32.0,
    ph_warn_low=7.5,
    ph_warn_high=8.5,
    salinity_warn_low=10.0,
)


def snap(avg_pump=1500.0, drop_rate=0.0, min_do=6.0, temp=29.0, ph=8.1, salinity=15.0):
    return PondSnapshot(
        "pond-01",
        avg_pump_watts=avg_pump,
        do_drop_rate=drop_rate,
        min_do=min_do,
        avg_temperature=temp,
        avg_ph=ph,
        avg_salinity=salinity,
    )


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


@pytest.mark.parametrize(
    ("kwargs", "root_cause"),
    [
        ({"temp": 36.5}, "temperature_out_of_range"),
        ({"temp": 24.0}, "temperature_out_of_range"),
        ({"ph": 8.9}, "ph_out_of_range"),
        ({"ph": 7.2}, "ph_out_of_range"),
        ({"salinity": 8.3}, "salinity_low"),
    ],
)
def test_environment_out_of_range_is_warning(kwargs, root_cause):
    f = evaluate(snap(**kwargs), T)
    assert (f.severity, f.root_cause) == (Severity.WARNING, root_cause)


def test_environment_at_threshold_is_normal():
    assert evaluate(snap(temp=32.0, ph=8.5, salinity=10.0), T) is None


def test_missing_environment_data_is_normal():
    assert evaluate(snap(temp=None, ph=None, salinity=None), T) is None


def test_oxygen_rules_take_priority_over_environment():
    f = evaluate(snap(avg_pump=0.0, drop_rate=0.1, temp=36.0, ph=9.0), T)
    assert f.root_cause == "pump_failure"
