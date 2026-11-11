import random
from datetime import datetime, timedelta

from app.domain.conditions import (
    CONDITIONS,
    ConditionType,
    REMEDIES,
    ActiveCondition,
    Remedy,
    Severity,
    active_conditions,
    applicable_remedies,
    at_full_intensity,
    build_condition,
    combined_effects,
    condition_intensity,
    conflicts_with,
    metric_changes,
)
from app.domain.models import MetricType
from app.domain.scenarios import SCENARIO_LIMITS, ScenarioType
from app.simulator.runner import MANUAL_RETEST_MIN, ponds_to_retest, technician_readings

NOW = datetime(2026, 11, 8, 12, 0, 0)


def _changes(*conditions: ConditionType) -> dict[str, tuple[str, float | None]]:
    return {c["metric"]: (c["direction"], c["offset"]) for c in metric_changes(list(conditions))}


def test_every_condition_changes_something():
    for condition in ConditionType:
        assert metric_changes([condition]) or CONDITIONS[condition].sensor_offline, condition
        assert CONDITIONS[condition].label


def test_sensor_offline_silences_the_probe_and_the_fix_ends_it():
    assert combined_effects([ConditionType.SENSOR_OFFLINE]).sensor_offline
    assert not combined_effects([ConditionType.HEAVY_RAIN]).sensor_offline
    assert applicable_remedies([ConditionType.SENSOR_OFFLINE]) == [Remedy.FIX_SENSOR]


def test_active_conditions_stay_on_and_skip_unknown_and_malformed():
    conditions = {
        "overfeeding": build_condition(NOW),
        "heavy_rain": {"started_at": (NOW - timedelta(days=3)).isoformat()},  # no expiry: still on days later
        "volcano": build_condition(NOW),  # unknown
        "cloudy": "bad",
    }
    assert build_condition(NOW) == {"started_at": NOW, "severity": "medium"}
    assert build_condition(NOW, Severity.SEVERE)["severity"] == "severe"
    running = active_conditions(conditions, NOW)
    assert [a.type for a in running] == [ConditionType.HEAVY_RAIN, ConditionType.OVERFEEDING]
    assert active_conditions(None, NOW) == []


def test_offsets_add_up_and_are_clamped_to_scenario_limits():
    effects = combined_effects([ConditionType.OVERFEEDING, ConditionType.HIGH_DENSITY])
    assert effects.sensor_offsets[ScenarioType.DO] == -3.0
    assert effects.manual_offsets == {MetricType.TAN: 1.8, MetricType.NITRITE: 0.8}
    assert effects.pump_failed is False

    worst = combined_effects(
        [ConditionType.OVERFEEDING, ConditionType.HIGH_DENSITY, ConditionType.CLOUDY, ConditionType.HOT_SUN]
    )
    assert worst.sensor_offsets[ScenarioType.DO] == SCENARIO_LIMITS[ScenarioType.DO].min_offset


def test_aerator_failure_stops_the_pump_and_drags_do_and_orp_down():
    changes = _changes(ConditionType.AERATOR_FAILURE, ConditionType.ALGAE_BLOOM)
    assert combined_effects([ConditionType.AERATOR_FAILURE]).pump_failed is True
    # Algae would raise DO, but without aeration DO keeps falling
    assert changes["do"] == ("down", None)
    assert changes["orp"] == ("down", None)
    assert changes["pump_power"] == ("down", None)


def test_derived_metrics_follow_the_chemistry():
    overfeeding = _changes(ConditionType.OVERFEEDING)
    assert overfeeding["tan"] == ("up", 1.2)
    assert overfeeding["nh3"] == ("up", None)
    heavy_rain = _changes(ConditionType.HEAVY_RAIN)
    assert heavy_rain["salinity"] == ("down", -6.0)
    assert heavy_rain["nh3"][0] == "down"  # cooler, lower pH -> less toxic ammonia
    assert "pump_power" not in heavy_rain
    assert _changes() == {}


def test_test_kit_readings_apply_condition_offsets_and_stay_positive():
    base = technician_readings({}, random.Random(1))
    shifted = technician_readings({MetricType.TAN: 1.2, MetricType.NITRITE: 0.6}, random.Random(1))
    assert shifted["tan"] == round(base["tan"] + 1.2, 2)
    assert shifted["alkalinity"] == base["alkalinity"]
    low = technician_readings({MetricType.TAN: -5.0, MetricType.NITRITE: -5.0, MetricType.ALKALINITY: -500.0})
    assert low["tan"] >= 0.05 and low["nitrite"] >= 0.02 and low["alkalinity"] >= 20.0


def test_new_conditions_move_metrics_the_expected_way():
    crash = _changes(ConditionType.ALGAE_CRASH)
    assert crash["do"] == ("down", -2.5) and crash["tan"][0] == "up" and crash["nh3"][0] == "up"
    assert _changes(ConditionType.DIRTY_BOTTOM)["orp"] == ("down", None)
    salty = _changes(ConditionType.SALINITY_RISE)
    assert salty["salinity"] == ("up", 6.0) and salty["temperature"][0] == "up"
    liming = _changes(ConditionType.LIMING)
    assert liming["ph"][0] == "up" and liming["alkalinity"][0] == "up" and liming["co2"][0] == "down"


def test_severity_scales_the_offsets():
    def do_offset(severity: Severity) -> float:
        return combined_effects([ActiveCondition(ConditionType.OVERFEEDING, severity)]).sensor_offsets[ScenarioType.DO]

    assert [do_offset(s) for s in Severity] == [-0.75, -1.5, -2.25]
    assert combined_effects([ActiveCondition(ConditionType.OVERFEEDING, Severity.SEVERE)]).manual_offsets[
        MetricType.TAN
    ] == round(1.2 * 1.5, 3)
    # Unknown severity falls back to medium
    bad = {"overfeeding": {"started_at": NOW, "severity": "apocalypse"}}
    assert active_conditions(bad, NOW)[0].severity is Severity.MEDIUM


def test_effect_builds_up_gradually():
    ramp = CONDITIONS[ConditionType.ALGAE_CRASH].ramp_min
    assert condition_intensity(ConditionType.ALGAE_CRASH, NOW, NOW) == 0.0
    assert condition_intensity(ConditionType.ALGAE_CRASH, NOW, NOW + timedelta(minutes=ramp / 2)) == 0.5
    assert condition_intensity(ConditionType.ALGAE_CRASH, NOW, NOW + timedelta(hours=10)) == 1.0

    half = active_conditions({"algae_crash": build_condition(NOW)}, NOW + timedelta(minutes=ramp / 2))
    full = combined_effects(at_full_intensity(half))
    now_effects = combined_effects(half)
    assert now_effects.sensor_offsets[ScenarioType.DO] == round(full.sensor_offsets[ScenarioType.DO] / 2, 3)
    # What the panel shows is the destination, not the current step
    assert _changes(*at_full_intensity(half))["do"] == ("down", -2.5)


def test_aerator_failure_is_immediate():
    started = active_conditions({"aerator_failure": build_condition(NOW)}, NOW)
    assert combined_effects(started).pump_failed is True


def test_one_off_actions_fade_out_and_expire():
    spec = CONDITIONS[ConditionType.LIMING]
    assert spec.duration_min
    stored = {"liming": build_condition(NOW)}
    start = combined_effects(active_conditions(stored, NOW)).manual_offsets[MetricType.ALKALINITY]
    mid = combined_effects(active_conditions(stored, NOW + timedelta(minutes=spec.duration_min / 2)))
    assert mid.manual_offsets[MetricType.ALKALINITY] == round(start / 2, 3)
    assert active_conditions(stored, NOW + timedelta(minutes=spec.duration_min)) == []


def test_remedies_end_the_conditions_they_fix():
    running = active_conditions(
        {"aerator_failure": build_condition(NOW), "dirty_bottom": build_condition(NOW), "cloudy": build_condition(NOW)},
        NOW,
    )
    assert applicable_remedies(running) == [Remedy.REPAIR_AERATOR, Remedy.CLEAN_BOTTOM, Remedy.BACKUP_AERATOR]
    assert applicable_remedies([]) == []
    assert all(info.clears and info.label for info in REMEDIES.values())


def test_retest_the_water_when_conditions_change_but_not_every_tick():
    off = {MetricType.TAN: 0.3}
    last_test = {"p1": NOW - timedelta(minutes=MANUAL_RETEST_MIN + 1), "p2": NOW - timedelta(minutes=1)}
    due = ponds_to_retest({"p1": off, "p2": off, "p3": {}}, {}, last_test, NOW)
    assert due == ["p1"]  # p2 was tested a minute ago; p3 has nothing new
    assert ponds_to_retest({"p1": off}, {"p1": off}, last_test, NOW) == []  # unchanged since the last test
    # A condition that ended is a change too
    assert ponds_to_retest({"p1": {}}, {"p1": off}, last_test, NOW) == ["p1"]
    # A remedy that ended a condition: test at once, even right after the last test
    assert ponds_to_retest({"p2": {}}, {"p2": off}, last_test, NOW) == []
    assert ponds_to_retest({"p2": {}}, {"p2": off}, last_test, NOW, ended={"p2"}) == ["p2"]


def test_liming_ends_the_rain_that_lowered_alkalinity():
    running = active_conditions({"heavy_rain": build_condition(NOW - timedelta(minutes=10), Severity.SEVERE)}, NOW)
    assert combined_effects(running).manual_offsets[MetricType.ALKALINITY] < 0
    assert applicable_remedies(running) == [Remedy.LIME]
    assert set(REMEDIES[Remedy.LIME].clears) == {ConditionType.HEAVY_RAIN, ConditionType.WATER_EXCHANGE}


def test_contradicting_conditions_are_exclusive():
    assert conflicts_with(ConditionType.HEAVY_RAIN) == [
        ConditionType.HOT_SUN,
        ConditionType.CLOUDY,
        ConditionType.SALINITY_RISE,
    ]
    assert conflicts_with(ConditionType.ALGAE_CRASH) == [ConditionType.ALGAE_BLOOM]
    assert conflicts_with(ConditionType.OVERFEEDING) == []
    for c in ConditionType:  # symmetric: if A ends B, B ends A
        assert all(c in conflicts_with(o) for o in conflicts_with(c))
