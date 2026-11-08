from dataclasses import replace

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
    orp_warn_low=100.0,
    nh3_warn=0.1,
    co2_warn=10.0,
    tan_warn=1.0,
    nitrite_warn=1.0,
    alkalinity_warn_low=120.0,
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
    assert f.root_cause == "do_out_of_range"


def test_fast_do_drop_with_running_pump_is_warning():
    f = evaluate(snap(drop_rate=0.08, min_do=5.0), T)
    assert (f.severity, f.root_cause) == (Severity.WARNING, "do_out_of_range")


@pytest.mark.parametrize(("min_do", "severity"), [(2.9, Severity.CRITICAL), (3.9, Severity.WARNING)])
def test_low_do_is_one_cause_with_severity_by_threshold(min_do, severity):
    f = evaluate(snap(min_do=min_do), T)
    assert (f.severity, f.root_cause) == (severity, "do_out_of_range")


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
        ({"salinity": 8.3}, "salinity_out_of_range"),
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


@pytest.mark.parametrize(
    ("kwargs", "root_cause"),
    [
        ({"nh3": 0.2}, "nh3_out_of_range"),
        ({"nitrite": 1.4}, "nitrite_out_of_range"),
        ({"tan": 1.8}, "tan_out_of_range"),
        ({"co2": 14.0}, "co2_out_of_range"),
        ({"last_orp": 60.0}, "orp_out_of_range"),
        ({"alkalinity": 90.0}, "alkalinity_out_of_range"),
    ],
)
def test_water_quality_out_of_range_is_warning(kwargs, root_cause):
    f = evaluate(replace(snap(), **kwargs), T)
    assert (f.severity, f.root_cause) == (Severity.WARNING, root_cause)


def test_water_quality_at_threshold_or_missing_is_normal():
    ok = replace(snap(), nh3=0.1, nitrite=1.0, tan=1.0, co2=10.0, last_orp=100.0, alkalinity=120.0)
    assert evaluate(ok, T) is None
    assert (
        evaluate(replace(snap(), nh3=None, nitrite=None, tan=None, co2=None, last_orp=None, alkalinity=None), T) is None
    )


def test_oxygen_and_environment_rules_come_before_water_quality():
    assert evaluate(replace(snap(min_do=3.5), nh3=0.5), T).root_cause == "do_out_of_range"
    assert evaluate(replace(snap(temp=36.0), nh3=0.5), T).root_cause == "temperature_out_of_range"
