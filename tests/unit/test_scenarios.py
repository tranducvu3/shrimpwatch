from datetime import datetime, timedelta

import pytest

from app.domain.scenarios import (
    SCENARIO_LIMITS,
    InvalidScenario,
    ScenarioType,
    active_offsets,
    build_scenario,
    validate_offset,
)

NOW = datetime(2026, 10, 31, 12, 0, 0)


def test_validate_offset_accepts_limits_and_rejects_beyond():
    for scenario, limit in SCENARIO_LIMITS.items():
        validate_offset(scenario, limit.min_offset)
        validate_offset(scenario, limit.max_offset)
        with pytest.raises(InvalidScenario):
            validate_offset(scenario, limit.max_offset + 0.01)
        with pytest.raises(InvalidScenario):
            validate_offset(scenario, limit.min_offset - 0.01)


def test_build_scenario_sets_end_time():
    doc = build_scenario(-2.0, 15, NOW)
    assert doc == {"offset": -2.0, "started_at": NOW, "until": NOW + timedelta(minutes=15)}


def test_active_offsets_skips_expired_unknown_and_malformed():
    scenarios = {
        "temperature": build_scenario(3.0, 10, NOW),
        "ph": {"offset": 0.5, "until": NOW - timedelta(seconds=1)},  # het han
        "salinity": {"offset": "x", "until": NOW + timedelta(minutes=5)},  # sai kieu
        "do": {"offset": -2, "until": "not-a-date"},
        "wind": {"offset": 1, "until": NOW + timedelta(minutes=5)},  # loai khong ton tai
    }
    assert active_offsets(scenarios, NOW) == {ScenarioType.TEMPERATURE: 3.0}


def test_active_offsets_parses_iso_strings_from_api():
    # Simulator nhan JSON tu GET /ponds: until la chuoi ISO (naive hoac co timezone)
    scenarios = {
        "do": {"offset": -3, "until": (NOW + timedelta(minutes=1)).isoformat()},
        "ph": {"offset": 0.4, "until": "2026-10-31T12:05:00+00:00"},
    }
    assert active_offsets(scenarios, NOW) == {ScenarioType.DO: -3.0, ScenarioType.PH: 0.4}


def test_active_offsets_handles_missing():
    assert active_offsets(None, NOW) == {}
